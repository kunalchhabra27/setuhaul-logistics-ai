from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from setuhaul.infrastructure.settings import Settings
from setuhaul.infrastructure.sms import normalize_phone, send_sms


def _settings(**overrides: Any) -> Settings:
    base = {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_x",
        "TWILIO_ACCOUNT_SID": None,
        "TWILIO_AUTH_TOKEN": None,
        "TWILIO_FROM_NUMBER": None,
    }
    base.update(overrides)
    return Settings.model_validate(base)


def test_normalize_phone_strips_dashes_and_keeps_plus() -> None:
    assert normalize_phone("+91-9000010001") == "+919000010001"
    assert normalize_phone("9000010001") == "+9000010001"
    assert normalize_phone("+1 (737) 250-8034") == "+17372508034"


def test_send_sms_skips_when_no_destination_number() -> None:
    settings = _settings(TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tok", TWILIO_FROM_NUMBER="+15551234567")
    assert send_sms(None, "hello", settings=settings) is None
    assert send_sms("", "hello", settings=settings) is None


def test_send_sms_skips_when_twilio_not_configured() -> None:
    settings = _settings()  # no sid/token/from
    assert send_sms("+919000010001", "hello", settings=settings) is None


def test_send_sms_skips_when_from_number_missing() -> None:
    settings = _settings(TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tok")
    assert send_sms("+919000010001", "hello", settings=settings) is None


def test_send_sms_success_returns_message_sid(monkeypatch) -> None:
    settings = _settings(TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tok", TWILIO_FROM_NUMBER="+15551234567")

    captured: dict[str, Any] = {}

    class FakeMessages:
        def create(self, *, to: str, from_: str, body: str):
            captured["to"] = to
            captured["from_"] = from_
            captured["body"] = body
            return SimpleNamespace(sid="SM_FAKE_123")

    class FakeClient:
        def __init__(self, account_sid: str, auth_token: str):
            captured["account_sid"] = account_sid
            captured["auth_token"] = auth_token
            self.messages = FakeMessages()

    import setuhaul.infrastructure.sms as sms_module

    monkeypatch.setattr(sms_module, "_get_client", lambda _settings: FakeClient(_settings.twilio_account_sid, _settings.twilio_auth_token))

    sid = send_sms("+91-9000010001", "Test message", settings=settings)

    assert sid == "SM_FAKE_123"
    assert captured["to"] == "+919000010001"
    assert captured["from_"] == "+15551234567"
    assert captured["body"] == "Test message"


def test_send_sms_swallows_twilio_errors(monkeypatch) -> None:
    settings = _settings(TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tok", TWILIO_FROM_NUMBER="+15551234567")

    class ExplodingMessages:
        def create(self, **_kwargs):
            raise RuntimeError("Twilio API is down")

    class ExplodingClient:
        def __init__(self):
            self.messages = ExplodingMessages()

    import setuhaul.infrastructure.sms as sms_module

    monkeypatch.setattr(sms_module, "_get_client", lambda _settings: ExplodingClient())

    assert send_sms("+919000010001", "Test message", settings=settings) is None
