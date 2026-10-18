"""
backend/app/services/email_service.py

Email alerting for high-urgency shelf reports.

Mirrors slack_service: the same alert that is posted to the Slack webhook is also
mailed to the login address(es) of the store the capture belongs to.

Two transports, chosen in this order so no SMTP account is ever required:

1. **Email API** — with ``RESEND_API_KEY`` set, delivery is a single HTTPS POST with
   an ``Authorization: Bearer`` header. No SMTP username, password or port.
2. **SMTP relay** — ``SMTP_HOST`` (+ optional ``SMTP_USER``/``SMTP_PASSWORD``) for a
   Gmail App Password, an Office 365 mailbox or an internal relay that needs no auth.

With neither configured the send is skipped (logged) and reported as *not sent*, so the
pipeline still runs end-to-end without any external call. Every public function returns
the recipient addresses that were actually delivered to and never raises: a mail outage
must never fail the capture pipeline.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Iterable, Sequence

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0

# Transactional endpoint (Resend by default); a plain HTTPS POST, no SDK required.
# settings.EMAIL_API_URL is overridable for a proxy or a local test double.

# Provider API keys look like "re_<20+ chars>". Checking the shape up front turns a
# silent HTTP 401 into an actionable "that is not a key" message.
_API_KEY_PATTERN = re.compile(r"^re_[A-Za-z0-9_\-]{16,}$")

# Why the last delivery attempt failed, if it did. Surfaced by /health and the email
# setup endpoint so "alerts raised but no mail" is explainable without reading logs.
_last_error: str | None = None
_last_error_at: datetime | None = None


def last_error() -> str | None:
    """Human-readable reason for the most recent failed delivery, or None."""
    return _last_error


def _record_error(message: str) -> None:
    global _last_error, _last_error_at
    _last_error = message
    _last_error_at = datetime.now(timezone.utc)


def _clear_error() -> None:
    global _last_error, _last_error_at
    _last_error = None
    _last_error_at = None


def clear_error() -> None:
    """Forget the last delivery failure (e.g. after new credentials are installed)."""
    _clear_error()


def normalize_api_key(raw: str | None) -> str:
    """Trim whitespace/quotes around a pasted key (a stray quote is a 401)."""
    return (raw or "").strip().strip('"').strip("'").strip()


def looks_like_api_key(raw: str | None) -> bool:
    return bool(_API_KEY_PATTERN.match(normalize_api_key(raw)))


def _api_configured() -> bool:
    return bool(settings.ALERT_EMAIL_ENABLED and normalize_api_key(settings.RESEND_API_KEY))


def _smtp_configured() -> bool:
    return bool(settings.ALERT_EMAIL_ENABLED and settings.SMTP_HOST and settings.smtp_from_address)


def config_error() -> str | None:
    """A configuration problem that would make every send fail, if there is one."""
    if not settings.ALERT_EMAIL_ENABLED:
        return "email alerts are disabled (ALERT_EMAIL_ENABLED=false)"
    key = normalize_api_key(settings.RESEND_API_KEY)
    if key and not _API_KEY_PATTERN.match(key):
        return (
            "the configured RESEND_API_KEY is not a valid Resend key "
            "(expected 're_' followed by at least 16 characters)"
        )
    return None


def is_configured() -> bool:
    """True when alert email can actually send over some transport.

    Returns a bool, not a message: callers use it as a yes/no (the health probe, the
    Email-setup button and ``POST /alerts/test``), so returning an error string here
    made a working relay look unconfigured and the UI kept asking for credentials
    again. The reason, when there is one, is :func:`config_error`.
    """
    return config_error() is None and (_api_configured() or _smtp_configured())


def _smtp_identity() -> dict | None:
    """The installation-wide SMTP relay from ``.env``, or None when unset.

    One relay serves every account: an SMTP password is a *sending* credential, so the
    same saved relay can deliver to any signed-in user's address. That is why this is a
    single shared identity rather than one credential per account.
    """
    if not _smtp_configured():
        return None
    return {
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "user": settings.SMTP_USER,
        "password": settings.SMTP_PASSWORD,
        "from_address": settings.smtp_from_address,
        "use_tls": settings.SMTP_USE_TLS,
        "use_ssl": settings.SMTP_USE_SSL,
    }


def alert_recipients(fallback: list[str] | None = None) -> list[str]:
    """Resolve the effective recipient list for an alert email.

    ALERT_EMAIL_TO (global override) wins when set — this is required for Resend's
    free tier, which only delivers to the Resend account's own address regardless of
    which store/user triggered the alert. Falls back to the provided list (typically
    the store's login addresses) when no override is configured.
    """
    override = settings.alert_email_recipients
    return override if override else (fallback or [])


def transport() -> str:
    """Which transport will be used: 'smtp', 'api', or 'none'.

    SMTP is reported first because it is what :func:`_deliver` tries first: an SMTP
    relay can mail any address, whereas a Resend key can only reach the account owner
    unless a domain is verified. Reporting the same order the sender uses keeps the
    dashboard honest about which path an alert will actually take.
    """
    if _smtp_configured():
        return "smtp"
    if _api_configured():
        return "api"
    return "none"


def _clean(addresses: Iterable[str | None]) -> list[str]:
    """Normalise, de-duplicate (case-insensitively) and drop blank addresses."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in addresses:
        address = (raw or "").strip()
        if not address:
            continue
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(address)
    return cleaned


def _build_message(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    # Already a full address or a "Name <addr>" pair — use it verbatim.
    message["From"] = settings.email_from_address
    message["To"] = to_email
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    return message


def _plain_and_html(message: EmailMessage) -> tuple[str, str | None]:
    """Split a built message into its plain-text and HTML bodies."""
    plain_part = message.get_body(preferencelist=("plain",))
    plain = plain_part.get_content() if plain_part is not None else ""
    html_part = message.get_body(preferencelist=("html",))
    html = html_part.get_content() if html_part is not None else None
    return plain, html


def _deliver_via_api(message: EmailMessage, recipient: str) -> bool:
    """POST the message to the email API. Returns True on acceptance."""
    plain, html = _plain_and_html(message)
    payload: dict = {
        "from": settings.email_from_address,
        "to": [recipient],
        "subject": message["Subject"],
        "text": plain,
    }
    if html:
        payload["html"] = html

    try:
        resp = httpx.post(
            settings.EMAIL_API_URL,
            headers={"Authorization": f"Bearer {normalize_api_key(settings.RESEND_API_KEY)}"},
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("email_service: HTTP error calling the email API: %s", exc)
        _record_error(f"could not reach the email provider: {exc.__class__.__name__}")
        return False

    if resp.status_code in (200, 201, 202):
        logger.info("email_service: alert email accepted by the email API for %s.", recipient)
        _clear_error()
        return True

    detail = resp.text[:300]
    logger.error(
        "email_service: email API rejected the message for %s (HTTP %s): %s",
        recipient,
        resp.status_code,
        detail,
    )
    if resp.status_code in (401, 403):
        _record_error(
            f"the email provider rejected the API key (HTTP {resp.status_code}); "
            "create a new key and save it again"
        )
    else:
        _record_error(f"the email provider rejected the message (HTTP {resp.status_code})")
    return False


def _deliver(message: EmailMessage, recipient: str) -> bool:
    """Deliver one message to `recipient` over whichever transport is configured.

    The transport is installation-wide (one SMTP relay or one API key, configured once);
    every recipient is reached through it. Which addresses receive an alert is decided by
    the caller from the store's account emails, so a new account is covered the moment it
    signs up — nothing has to be re-entered per user.
    """
    # 1. SMTP relay from the .env settings (Gmail App Password etc.).
    smtp_identity = _smtp_identity()
    if smtp_identity is not None:
        if _send_via_smtp(message, recipient, smtp_identity):
            return True
        # SMTP configured but delivery failed — still try the API transport below if a
        # key is present, so one broken relay does not silently drop every alert.

    # 2. Shared Email API (Resend key).
    if _api_configured() and _deliver_via_api(message, recipient):
        return True

    # 3. Nothing reached this recipient.
    return False


def _send_via_smtp(message: EmailMessage, recipient: str, identity: dict) -> bool:
    """Try to deliver one message through the saved SMTP relay."""
    context = ssl.create_default_context()
    host = identity["host"]
    port = identity["port"]

    try:
        if identity["use_ssl"]:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, timeout=_TIMEOUT_SECONDS, context=context
            )
        else:
            client = smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS)

        with client:
            if not identity["use_ssl"] and identity["use_tls"]:
                client.starttls(context=context)
            if identity["user"] and identity["password"]:
                client.login(identity["user"], identity["password"])
            client.send_message(
                message,
                from_addr=identity["from_address"],
                to_addrs=[recipient],
            )
    except (smtplib.SMTPException, OSError) as exc:
        logger.error(
            "email_service: SMTP delivery to %s failed via %s as %s: %s",
            recipient,
            identity["host"],
            identity["user"],
            exc,
        )
        return False

    logger.info(
        "email_service: alert email delivered to %s via %s as %s.",
        recipient,
        identity["host"],
        identity["user"],
    )
    return True


def _send(
    *,
    to_emails: Sequence[str | None],
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> list[str]:
    """Send `subject`/`body` to each address, returning those that were delivered."""
    recipients = _clean(to_emails)
    if not recipients:
        logger.warning("email_service: no recipient address available — email send skipped.")
        return []
    if not is_configured():
        # Say it once, loudly: the pipeline is fine, but no mail can leave the building
        # until one transport is set up. This is the most common reason alerts sit at
        # "Not sent" with nothing in the logs.
        logger.warning(
            "email_service: no email transport configured — %s.",
            config_error() or "set SMTP_HOST/SMTP_USER/SMTP_PASSWORD or RESEND_API_KEY",
        )
        return []

    delivered: list[str] = []
    for recipient in recipients:
        message = _build_message(
            to_email=recipient,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        if _deliver(message, recipient):
            delivered.append(recipient)
    return delivered


def notify_high_urgency(
    *,
    to_emails: Sequence[str | None],
    aisle: str,
    fill_percentage: float,
    summary: str,
    recommended_action: str,
    store_name: str | None = None,
) -> list[str]:
    """Email a high-urgency restock alert. Returns the addresses it reached."""
    fill_pct = fill_percentage * 100
    location = f"{store_name} · {aisle}" if store_name else aisle
    subject = f"[ShelfMonitor] High urgency — {location} ({fill_pct:.1f}% fill)"

    text_body = (
        f"High urgency restock alert\n"
        f"\n"
        f"Location:            {location}\n"
        f"Fill level:          {fill_pct:.1f}%\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"Recommended action:  {recommended_action}\n"
        f"\n"
        f"— ShelfMonitor (Restocking & Shelf Management Assistant)\n"
        f"This alert was also sent to your store's Slack channel.\n"
    )

    html_body = f"""\
<html>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Arial, sans-serif; color: #111827;">
    <h2 style="color:#b91c1c;margin:0 0 4px;">High urgency restock alert</h2>
    <p style="margin:0 0 16px;color:#4b5563;">{location}</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;margin-bottom:16px;">
      <tr>
        <td style="color:#6b7280;">Fill level</td>
        <td style="font-weight:600;">{fill_pct:.1f}%</td>
      </tr>
    </table>
    <p style="margin:0 0 12px;">{summary}</p>
    <p style="margin:0 0 20px;">
      <strong>Recommended action:</strong> {recommended_action}
    </p>
    <p style="margin:0;color:#6b7280;font-size:12px;">
      — ShelfMonitor (Restocking &amp; Shelf Management Assistant). This alert was also
      sent to your store's Slack channel.
    </p>
  </body>
</html>
"""

    return _send(
        to_emails=to_emails,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


def send_test_message(
    to_emails: Sequence[str | None],
    *,
    store_name: str | None = None,
) -> list[str]:
    """Send a diagnostic email so an operator can confirm delivery works."""
    where = f" for {store_name}" if store_name else ""
    subject = "[ShelfMonitor] Test alert — email delivery is configured"
    text_body = (
        f"This is a test alert{where} from ShelfMonitor.\n"
        f"\n"
        f"If you can read this, high-urgency restock alerts will arrive at this "
        f"login address automatically.\n"
        f"\n"
        f"— ShelfMonitor (Restocking & Shelf Management Assistant)\n"
    )
    return _send(to_emails=to_emails, subject=subject, text_body=text_body)
