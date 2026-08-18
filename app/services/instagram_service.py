"""Instagram Graph API client with retry and error handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

# Appended to every public comment reply.
COMMENT_CONTACT_FOOTER = (
    "Doktor Sultonbekni shaxsan o'zlari bilan bog'lanish uchun "
    "+998776906006 raqami orqali telegramdan yoki docsulton.uz "
    "saytidan foydalanishingiz mumkin."
)


def _redact_url(url: str) -> str:
    """Remove access tokens from logged URLs."""
    if "access_token=" not in url:
        return url
    base, _, _ = url.partition("access_token=")
    return f"{base}access_token=***REDACTED***"


def append_comment_contact_footer(message: str) -> str:
    """Ensure the clinic contact footer is at the end of a public comment reply."""
    body = (message or "").strip()
    footer = COMMENT_CONTACT_FOOTER
    if footer in body:
        return body
    if not body:
        return footer
    return f"{body}\n\n{footer}"


def sanitize_text_for_instagram_links(text: str) -> str:
    """Strip protocol schemes and domain formatting if Meta API blocks raw URL link sharing."""
    import re
    cleaned = (text or "").strip()
    cleaned = re.sub(r"https?://(?:www\.)?t\.me/([a-zA-Z0-9_]+)", r"Telegram: \1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"t\.me/([a-zA-Z0-9_]+)", r"Telegram: \1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"https?://(?:www\.)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([a-zA-Z0-9]+)\.uz", r"\1 uz", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([a-zA-Z0-9]+)\.com", r"\1 com", cleaned, flags=re.IGNORECASE)
    return cleaned


class InstagramAPIError(Exception):
    """Raised when the Instagram Graph API returns an error."""

    # Permanent Graph errors — never retry these (burns Gemini + delays fresh mail).
    PERMANENT_ERROR_CODES = frozenset({
        10,   # permission / messaging window
        100,  # unsupported / object missing
        190,  # invalid OAuth token
        200,  # permission
        230,  # user consent required
    })
    PERMANENT_SUBCODES = frozenset({
        33,       # object does not exist / missing permissions
        2534022,  # outside messaging window
    })

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        error_subcode: int | None = None,
        response_body: dict[str, Any] | str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_subcode = error_subcode
        self.response_body = response_body

    @property
    def is_permanent(self) -> bool:
        if self.error_subcode in self.PERMANENT_SUBCODES:
            return True
        if self.error_code in self.PERMANENT_ERROR_CODES:
            return True
        text = str(self).lower()
        return (
            "outside of allowed window" in text
            or "user consent is required" in text
            or "does not exist" in text
            or "missing permissions" in text
        )


def is_permanent_instagram_error(exc: BaseException) -> bool:
    return isinstance(exc, InstagramAPIError) and exc.is_permanent


class InstagramService:
    """
    Instagram Graph API service.

    Handles comment replies, media lookups, messaging, and resilient HTTP communication.
    """

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.meta_graph_base_url
        self._access_token = settings.meta_access_token.strip()
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_max_retries
        self._cached_user_id: str | None = None
        self._cached_username: str | None = None

    def _build_url(self, path: str) -> str:
        """Build a full Graph API URL."""
        return f"{self._base_url}/{path.lstrip('/')}"

    def handle_errors(self, response: httpx.Response) -> None:
        """
        Parse an API response and raise InstagramAPIError on failure.

        Logs the full response body for observability.
        """
        log_event(
            logger,
            logging.INFO,
            "instagram_api_response",
            status_code=response.status_code,
            url=_redact_url(str(response.url)),
            body=response.text[:2000],
        )

        if response.is_success:
            return

        error_body: dict[str, Any] | str
        try:
            error_body = response.json()
        except Exception:
            error_body = response.text

        error_code: int | None = None
        error_subcode: int | None = None
        error_message = f"Instagram API error (HTTP {response.status_code})"

        if isinstance(error_body, dict):
            error_obj = error_body.get("error", {})
            if isinstance(error_obj, dict):
                error_code = error_obj.get("code")
                error_subcode = error_obj.get("error_subcode")
                error_message = error_obj.get("message", error_message)

        raise InstagramAPIError(
            error_message,
            status_code=response.status_code,
            error_code=error_code,
            error_subcode=error_subcode,
            response_body=error_body,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request with exponential-backoff retry."""
        url = self._build_url(path)
        query_params = {"access_token": self._access_token, **(params or {})}
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, self._max_retries + 1):
                try:
                    response = await client.request(
                        method,
                        url,
                        params=query_params,
                        json=json_body,
                    )
                    self.handle_errors(response)
                    return response.json()

                except InstagramAPIError as exc:
                    last_error = exc
                    # Permanent Graph errors often arrive as HTTP 500 — never retry them.
                    retryable = (
                        not exc.is_permanent
                        and exc.status_code in self.RETRYABLE_STATUS_CODES
                        and attempt < self._max_retries
                    )
                    if not retryable:
                        log_event(
                            logger,
                            logging.ERROR,
                            "instagram_api_error",
                            attempt=attempt,
                            status_code=exc.status_code,
                            error_code=exc.error_code,
                            error_subcode=exc.error_subcode,
                            error_detail=str(exc),
                            permanent=exc.is_permanent,
                        )
                        raise

                    wait = 2**attempt
                    log_event(
                        logger,
                        logging.WARNING,
                        "instagram_api_retry",
                        attempt=attempt,
                        wait_seconds=wait,
                        status_code=exc.status_code,
                    )
                    await asyncio.sleep(wait)

                except httpx.RequestError as exc:
                    last_error = exc
                    if attempt == self._max_retries:
                        log_event(
                            logger,
                            logging.ERROR,
                            "instagram_network_error",
                            attempt=attempt,
                            error_detail=str(exc),
                        )
                        raise InstagramAPIError(
                            f"Network error: {exc}",
                            response_body=str(exc),
                        ) from exc

                    wait = 2**attempt
                    log_event(
                        logger,
                        logging.WARNING,
                        "instagram_network_retry",
                        attempt=attempt,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)

        raise InstagramAPIError(
            f"Request failed after {self._max_retries} attempts: {last_error}"
        )

    async def get_authenticated_user_id(self) -> str:
        """Return the authenticated Instagram user ID (cached or from /me)."""
        if self._cached_user_id:
            return self._cached_user_id

        configured = self._settings.resolved_instagram_user_id
        if configured:
            self._cached_user_id = configured
            return configured

        profile = await self.validate_token()
        user_id = str(profile.get("user_id") or profile.get("id") or "")
        self._cached_user_id = user_id
        self._cached_username = profile.get("username")
        return user_id

    async def fetch_comment_details(self, comment_id: str) -> dict[str, Any]:
        """
        Fetch comment metadata with detailed request/response logging.

        Logs URL, HTTP status, and full body. On 400/403 logs the complete error
        without suppressing details. Returns parsed JSON on success.
        """
        path = comment_id
        params = {
            "access_token": self._access_token,
            "fields": "id,text,from,parent_id,media",
        }
        url = self._build_url(path)

        log_event(
            logger,
            logging.INFO,
            "comment_fetch_request",
            comment_id=comment_id,
            request_url=_redact_url(f"{url}?fields=id,text,from,parent_id,media&access_token=***"),
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, params=params)

        log_event(
            logger,
            logging.INFO,
            "comment_fetch_response",
            comment_id=comment_id,
            status_code=response.status_code,
            request_url=_redact_url(str(response.url)),
            response_body=response.text[:4000],
        )

        if response.status_code in (400, 403):
            log_event(
                logger,
                logging.ERROR,
                "comment_fetch_error",
                comment_id=comment_id,
                status_code=response.status_code,
                full_error_body=response.text,
            )
            try:
                error_json = response.json()
            except Exception:
                error_json = response.text
            raise InstagramAPIError(
                f"Comment fetch failed (HTTP {response.status_code})",
                status_code=response.status_code,
                response_body=error_json,
            )

        self.handle_errors(response)
        return response.json()

    async def send_message(self, recipient_id: str, text: str) -> dict[str, Any]:
        """
        Send an Instagram Direct Message.

        Uses POST /{ig-user-id}/messages with the Instagram Messaging API.
        """
        ig_user_id = await self.get_authenticated_user_id()
        recipient_id = str(recipient_id).strip()
        if not recipient_id:
            raise InstagramAPIError("DM recipient_id is empty")
        if recipient_id == ig_user_id:
            raise InstagramAPIError(
                f"Refusing to send DM to business account id {recipient_id}; use sender_id from webhook"
            )
        log_event(
            logger,
            logging.INFO,
            "instagram_send_message_request",
            recipient_id=recipient_id,
            reply_text=text,
            ig_user_id=ig_user_id,
        )
        try:
            result = await self._request(
                "POST",
                f"{ig_user_id}/messages",
                json_body={
                    "recipient": {"id": recipient_id},
                    "message": {"text": text},
                },
            )
        except InstagramAPIError as exc:
            if exc.error_subcode == 2534122 or exc.error_code == 508 or "link can't be shared" in str(exc).lower():
                sanitized = sanitize_text_for_instagram_links(text)
                log_event(
                    logger,
                    logging.WARNING,
                    "instagram_link_blocked_sanitizing",
                    recipient_id=recipient_id,
                    original_text=text,
                    sanitized_text=sanitized,
                    error=str(exc),
                )
                result = await self._request(
                    "POST",
                    f"{ig_user_id}/messages",
                    json_body={
                        "recipient": {"id": recipient_id},
                        "message": {"text": sanitized},
                    },
                )
            else:
                raise
        log_event(
            logger,
            logging.INFO,
            "instagram_send_message_success",
            recipient_id=recipient_id,
            response=result,
        )
        return result

    async def validate_token(self) -> dict[str, Any]:
        """
        Verify the access token against the Instagram Graph API.

        Logs account info on success; raises InstagramAPIError on failure.
        """
        return await self._request(
            "GET",
            "me",
            params={"fields": "user_id,username,name"},
        )

    async def reply_comment(
        self,
        comment_id: str,
        message: str,
    ) -> dict[str, Any]:
        """
        Post a reply to an Instagram comment.

        Uses POST /{comment-id}/replies?message=... per Instagram Graph API.
        Always appends the clinic contact footer.
        """
        message = append_comment_contact_footer(message)
        log_event(
            logger,
            logging.INFO,
            "instagram_reply_request",
            comment_id=comment_id,
            reply_text=message,
        )
        result = await self._request(
            "POST",
            f"{comment_id}/replies",
            params={"message": message},
        )
        log_event(
            logger,
            logging.INFO,
            "instagram_reply_success",
            comment_id=comment_id,
            response=result,
        )
        return result

    async def send_private_reply_to_comment(
        self,
        comment_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Send a private DM reply tied to a comment (caption/mention use cases)."""
        ig_user_id = await self.get_authenticated_user_id()
        log_event(
            logger,
            logging.INFO,
            "instagram_private_reply_request",
            comment_id=comment_id,
            reply_text=text,
            ig_user_id=ig_user_id,
        )
        result = await self._request(
            "POST",
            f"{ig_user_id}/messages",
            json_body={
                "recipient": {"comment_id": comment_id},
                "message": {"text": text},
            },
        )
        log_event(
            logger,
            logging.INFO,
            "instagram_private_reply_success",
            comment_id=comment_id,
            response=result,
        )
        return result

    async def fetch_user_profile(
        self,
        user_id: str,
        *,
        fallback_username: str | None = None,
    ) -> dict[str, Any]:
        """
        Fetch public profile details for a commenter or DM sender.

        Uses Instagram User Profile fields available after the user interacts
        with your account (name, username, follower count, follow status).
        """
        fields = (
            "name,username,profile_pic,follower_count,"
            "is_user_follow_business,is_business_follow_user,is_verified_user"
        )
        try:
            profile = await self._request(
                "GET",
                user_id,
                params={"fields": fields},
            )
            log_event(
                logger,
                logging.INFO,
                "user_profile_fetched",
                user_id=user_id,
                username=profile.get("username"),
                name=profile.get("name"),
            )
            return profile
        except InstagramAPIError as exc:
            log_event(
                logger,
                logging.WARNING,
                "user_profile_fetch_failed",
                user_id=user_id,
                error=str(exc),
                status_code=exc.status_code,
            )
            return {
                "id": user_id,
                "username": fallback_username or "",
            }

    async def resolve_media_id_from_url(self, url: str) -> str:
        """Resolve Instagram media id from a reel/post URL."""
        import re

        match = re.search(r"instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)", url)
        if not match:
            raise InstagramAPIError(f"Could not parse Instagram URL: {url}")
        shortcode = match.group(1)
        ig_user_id = await self.get_authenticated_user_id()

        result = await self._request(
            "GET",
            f"{ig_user_id}/media",
            params={"fields": "id,permalink,shortcode", "limit": 100},
        )
        for item in result.get("data", []):
            permalink = str(item.get("permalink", ""))
            if shortcode in permalink or item.get("shortcode") == shortcode:
                return str(item["id"])

        raise InstagramAPIError(f"Media not found for URL: {url}")

    async def get_media(self, media_id: str) -> dict[str, Any]:
        """Fetch metadata for an Instagram media object."""
        return await self._request(
            "GET",
            media_id,
            params={
                "fields": (
                    "id,caption,media_type,media_product_type,permalink,"
                    "timestamp,like_count,comments_count,username"
                )
            },
        )

    async def get_comment(self, comment_id: str) -> dict[str, Any]:
        """Fetch metadata for an Instagram comment."""
        return await self._request(
            "GET",
            comment_id,
            params={"fields": "id,text,timestamp,from,parent_id,media"},
        )
