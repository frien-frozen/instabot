"""Instagram Meta webhook routes."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings
from app.dependencies import get_event_dispatcher
from app.services.event_dispatcher import EventDispatcher
from app.utils.logging import get_logger, log_event
from app.utils.webhook_logging import log_all_webhook_events

logger = get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    """Meta webhook verification endpoint (GET)."""
    log_event(logger, logging.INFO, "webhook_verification_attempt", hub_mode=hub_mode)

    if hub_mode == "subscribe" and hub_verify_token == settings.verify_token:
        log_event(logger, logging.INFO, "webhook_verification_success")
        return PlainTextResponse(content=hub_challenge, status_code=200)

    log_event(logger, logging.WARNING, "webhook_verification_failed")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch",
    )


@router.post("")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    dispatcher: EventDispatcher = Depends(get_event_dispatcher),
) -> dict[str, Any]:
    """Receive Instagram webhook events (POST)."""
    raw_body = await request.body()
    headers = dict(request.headers)
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = headers.get("x-forwarded-for", "")

    logger.info("RAW BODY: %r", raw_body)
    logger.info("HEADERS: %s", headers)
    log_event(
        logger,
        logging.INFO,
        "webhook_post_received",
        client_ip=client_ip,
        forwarded_for=forwarded_for,
        content_length=len(raw_body),
        user_agent=headers.get("user-agent", ""),
    )

    if not raw_body:
        logger.warning("Empty webhook payload from %s", client_ip)
        return {"ok": False, "error": "empty_payload"}

    try:
        body: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.exception("JSON parse failed")
        return {"ok": False, "error": "invalid_json"}

    if not isinstance(body, dict):
        return {"ok": False, "error": "invalid_json"}

    log_all_webhook_events(body, client_ip=client_ip)

    from app.adapters.instagram import InstagramAdapter

    body = InstagramAdapter().normalize_body(body, settings)

    if body.get("object") != "instagram":
        return {"status": "ignored", "reason": "unsupported_object"}

    events = dispatcher.parse_events(body)
    counts = await dispatcher.dispatch_webhook(body)

    for event in events:
        background_tasks.add_task(dispatcher.dispatch, event)

    return {
        "status": "ok",
        "comments_queued": str(counts["comment"]),
        "messages_queued": str(counts["message"]),
        "mentions_queued": str(counts["mention"]),
        "total_queued": str(counts["total"]),
    }
