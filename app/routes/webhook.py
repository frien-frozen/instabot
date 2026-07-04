"""Instagram Meta webhook routes."""

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings
from app.dependencies import get_comment_processor
from app.schemas import CommentCreate, InstagramWebhookPayload
from app.services.comment_processor import CommentProcessor
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

# Supported Instagram webhook fields
SUPPORTED_FIELDS = frozenset({"comments", "live_comments"})


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    """
    Meta webhook verification endpoint (GET).

    Meta sends a challenge that must be echoed back when the verify token matches.
    """
    log_event(
        logger,
        logging.INFO,
        "webhook_verification_attempt",
        hub_mode=hub_mode,
    )

    if hub_mode == "subscribe" and hub_verify_token == settings.verify_token:
        log_event(logger, logging.INFO, "webhook_verification_success")
        return PlainTextResponse(content=hub_challenge, status_code=200)

    log_event(logger, logging.WARNING, "webhook_verification_failed")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch",
    )


def _extract_comments(payload: InstagramWebhookPayload) -> list[CommentCreate]:
    """Parse webhook payload and extract comment records."""
    comments: list[CommentCreate] = []

    if payload.object != "instagram":
        return comments

    for entry in payload.entry:
        account_id = entry.id

        if not entry.changes:
            continue

        for change in entry.changes:
            if change.field not in SUPPORTED_FIELDS:
                log_event(
                    logger,
                    logging.DEBUG,
                    "unsupported_webhook_field",
                    field=change.field,
                )
                continue

            value = change.value
            if isinstance(value, dict):
                comment_id = value.get("id")
                if not comment_id:
                    continue

                from_user = value.get("from", {})
                media = value.get("media", {})

                comments.append(
                    CommentCreate(
                        comment_id=str(comment_id),
                        username=from_user.get("username", "unknown"),
                        message=value.get("text", ""),
                        media_id=str(media.get("id", "")),
                        parent_comment_id=value.get("parent_id"),
                        account_id=account_id,
                    )
                )

    return comments


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    processor: CommentProcessor = Depends(get_comment_processor),
) -> dict[str, str]:
    """
    Receive Instagram webhook events (POST).

    Acknowledges immediately and processes comments in the background
    to meet Meta's 20-second response requirement.
    """
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty webhook payload",
        )

    try:
        body: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        log_event(
            logger,
            logging.ERROR,
            "webhook_parse_error",
            error=str(exc),
            raw_body=raw_body.decode("utf-8", errors="replace")[:2000],
            headers=dict(request.headers),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    if not isinstance(body, dict):
        log_event(
            logger,
            logging.ERROR,
            "webhook_parse_error",
            error="Payload must be a JSON object",
            raw_body=raw_body.decode("utf-8", errors="replace")[:2000],
            headers=dict(request.headers),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    log_event(
        logger,
        logging.INFO,
        "webhook_received",
        object_type=body.get("object"),
        entry_count=len(body.get("entry", [])),
    )

    try:
        payload = InstagramWebhookPayload.model_validate(body)
    except Exception as exc:
        log_event(logger, logging.WARNING, "webhook_validation_warning", error=str(exc))
        # Return 200 anyway so Meta doesn't disable the webhook
        return {"status": "ignored", "reason": "invalid_payload"}

    if payload.object != "instagram":
        log_event(
            logger,
            logging.DEBUG,
            "unsupported_webhook_object",
            object_type=payload.object,
        )
        return {"status": "ignored", "reason": "unsupported_object"}

    comments = _extract_comments(payload)

    if not comments:
        return {"status": "ok", "processed": "0"}

    for comment_data in comments:
        background_tasks.add_task(processor.process, comment_data)

    log_event(
        logger,
        logging.INFO,
        "webhook_queued",
        comment_count=len(comments),
    )

    return {"status": "ok", "processed": str(len(comments))}
