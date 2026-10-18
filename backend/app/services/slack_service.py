"""
backend/app/services/slack_service.py

Slack alerting for high-urgency shelf reports.

Posts messages to the Slack Incoming Webhook configured in settings.SLACK_WEBHOOK_URL.
When no webhook is configured the send is skipped (logged) and reported as *not sent*,
so the pipeline still runs end-to-end without any external call.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Slack replies to a webhook POST with HTTP 200 and the literal body "ok" on success.
_SLACK_OK_BODY = "ok"
_TIMEOUT_SECONDS = 10.0


def _post(text: str) -> bool:
    """POST `text` to the configured Slack webhook. Returns True only on success."""
    webhook = settings.SLACK_WEBHOOK_URL
    if not webhook:
        logger.warning("slack_service: SLACK_WEBHOOK_URL not set — Slack send skipped.")
        return False

    try:
        resp = httpx.post(webhook, json={"text": text}, timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.error("slack_service: HTTP error posting to Slack: %s", exc)
        return False

    if resp.status_code == 200 and resp.text.strip() == _SLACK_OK_BODY:
        logger.info("slack_service: Slack message delivered.")
        return True

    logger.error(
        "slack_service: Slack rejected the message (HTTP %s): %s",
        resp.status_code,
        resp.text[:200],
    )
    return False


def notify_high_urgency(
    *,
    aisle: str,
    fill_percentage: float,
    summary: str,
    recommended_action: str,
) -> bool:
    """Send a high-urgency restock alert to Slack. Returns True if delivered."""
    text = (
        f":rotating_light: *High urgency — {aisle}* "
        f"({fill_percentage * 100:.1f}% fill)\n"
        f"{summary}\n"
        f"*Recommended action:* {recommended_action}"
    )
    return _post(text)


def send_test_message(text: Optional[str] = None) -> bool:
    """Post a diagnostic message so an operator can confirm the webhook works."""
    message = text or (
        ":white_check_mark: Test alert from *Retail Shelf Monitoring* — "
        "your Slack webhook is configured correctly."
    )
    return _post(message)
