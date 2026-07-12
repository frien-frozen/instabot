"""Enqueue-only Instagram webhook — returns 200 immediately."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.instagram.parser import WebhookParser
from app.repositories.event_repository import EventRepository
from app.gemini_config import is_gemini_ready
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
    if hub_mode == "subscribe" and hub_verify_token == settings.verify_token:
        return PlainTextResponse(content=hub_challenge, status_code=200)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token mismatch")


@router.post("")
async def receive_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Save events to queue and return immediately."""
    if not is_gemini_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini model not validated; webhook temporarily unavailable",
        )

    started = time.monotonic()
    raw_body = await request.body()

    if not raw_body:
        return {"status": "ok", "queued": "0"}

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"status": "ok", "queued": "0", "error": "invalid_json"}

    if not isinstance(body, dict):
        return {"status": "ok", "queued": "0"}

    log_all_webhook_events(body, client_ip=request.client.host if request.client else "unknown")

    parser = WebhookParser(settings)
    parsed_events = parser.parse(body)

    queued = 0
    duplicates = 0
    factory = get_session_factory(settings)
    async with factory() as session:
        repo = EventRepository(session)
        for parsed in parsed_events:
            row = await repo.enqueue(parsed)
            if row is None:
                duplicates += 1
            else:
                queued += 1
        await session.commit()

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log_event(
        logger,
        logging.INFO,
        "webhook_enqueued",
        queued=queued,
        duplicates=duplicates,
        elapsed_ms=elapsed_ms,
    )

    return {"status": "ok", "queued": str(queued), "duplicates": str(duplicates), "elapsed_ms": str(elapsed_ms)}
