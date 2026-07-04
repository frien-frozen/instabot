"""Instagram Graph API client with retry and error handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)


def _redact_url(url: str) -> str:
    """Remove access tokens from logged URLs."""
    if "access_token=" not in url:
        return url
    base, _, _ = url.partition("access_token=")
    return f"{base}access_token=***REDACTED***"


class InstagramAPIError(Exception):
    """Raised when the Instagram Graph API returns an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        response_body: dict[str, Any] | str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response_body = response_body


class InstagramService:
    """
    Instagram Graph API service.

    Handles comment replies, media lookups, and resilient HTTP communication.
    Future multi-account support: pass account-specific access tokens.
    """

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.meta_graph_base_url
        self._access_token = settings.meta_access_token.strip()
        self._timeout = settings.http_timeout_seconds
        self._max_retries = settings.http_max_retries

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
        error_message = f"Instagram API error (HTTP {response.status_code})"

        if isinstance(error_body, dict):
            error_obj = error_body.get("error", {})
            if isinstance(error_obj, dict):
                error_code = error_obj.get("code")
                error_message = error_obj.get("message", error_message)

        raise InstagramAPIError(
            error_message,
            status_code=response.status_code,
            error_code=error_code,
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
                    if (
                        exc.status_code not in self.RETRYABLE_STATUS_CODES
                        or attempt == self._max_retries
                    ):
                        log_event(
                            logger,
                            logging.ERROR,
                            "instagram_api_error",
                            attempt=attempt,
                            status_code=exc.status_code,
                            error_code=exc.error_code,
                            error_detail=str(exc),
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
        """
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

    async def get_media(self, media_id: str) -> dict[str, Any]:
        """Fetch metadata for an Instagram media object."""
        return await self._request(
            "GET",
            media_id,
            params={"fields": "id,caption,media_type,permalink,timestamp"},
        )

    async def get_comment(self, comment_id: str) -> dict[str, Any]:
        """Fetch metadata for an Instagram comment."""
        return await self._request(
            "GET",
            comment_id,
            params={"fields": "id,text,timestamp,from,parent_id,media"},
        )
