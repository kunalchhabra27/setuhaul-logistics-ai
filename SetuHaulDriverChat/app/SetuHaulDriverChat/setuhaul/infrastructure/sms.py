"""Best-effort SMS notifications via Twilio.

Used for two courtesy notifications: a shipment checking in at a facility
gate, and a driver being assigned to a shipment. Neither of those is the
actual business transaction (the check-in row / the driver assignment is
already committed to Supabase by the time this module is called) -- so a
missing Twilio configuration, a transient Twilio API error, or a driver with
no phone number on file must never fail the caller's request. Every failure
mode here is caught and logged, and callers should treat the return value as
fire-and-forget (a message SID on success, None otherwise).

Credentials are read from environment variables only (TWILIO_ACCOUNT_SID,
TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER via infrastructure.settings) -- never
hardcode real Twilio credentials in source. Set real values in your own
untracked .env file, matching the pattern already used for SUPABASE_* and
GOOGLE_API_KEY in that module.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from setuhaul.infrastructure.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def normalize_phone(phone: str) -> str:
    """Reduce a stored phone number like '+91-9000010001' to Twilio's
    expected E.164 shape ('+919000010001') -- strip everything but digits
    and a single leading '+'."""
    digits = re.sub(r"[^\d+]", "", phone)
    if not digits.startswith("+"):
        digits = f"+{digits}"
    return digits


def _get_client(settings: Settings) -> Any | None:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.info("Twilio is not configured (missing account SID/auth token); skipping SMS.")
        return None
    try:
        from twilio.rest import Client
    except ModuleNotFoundError:
        logger.warning("twilio package is not installed; skipping SMS.")
        return None
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def send_sms(to: str | None, body: str, *, settings: Settings | None = None) -> str | None:
    """Send an SMS via Twilio. Returns the Twilio message SID on success, or
    None if SMS could not be sent for any reason (unconfigured, no
    destination number, or a Twilio API error) -- never raises."""
    if not to:
        logger.info("Skipping SMS (no destination phone number on file): %r", body)
        return None

    settings = settings or get_settings()
    if not settings.twilio_from_number:
        logger.warning("Skipping SMS to %s: TWILIO_FROM_NUMBER is not set.", to)
        return None

    client = _get_client(settings)
    if client is None:
        return None

    try:
        message = client.messages.create(
            to=normalize_phone(to),
            from_=settings.twilio_from_number,
            body=body,
        )
        return message.sid
    except Exception:  # noqa: BLE001 - any Twilio/network failure must not break the caller
        logger.exception("Failed to send SMS to %s", to)
        return None
