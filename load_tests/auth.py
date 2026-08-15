"""Process-wide Supabase authentication for the local Locust harness."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx


_REFRESH_WINDOW_SECONDS = 90


class LoadTestAuthenticationError(RuntimeError):
    """A safe, operator-facing load-test authentication error."""


@dataclass(frozen=True)
class _Session:
    access_token: str
    refresh_token: str
    expires_at: float


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _jwt_expiration(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expiration = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(expiration) if expiration is not None else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None


class LoadTestAuth:
    """Acquire and refresh one in-memory Supabase session per service role."""

    def __init__(
        self,
        *,
        post: Callable[..., httpx.Response] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._post = post or httpx.post
        self._now = now
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def access_token(self, role: str | None = None) -> str:
        normalized_role = (role or "generic").strip().lower()
        explicit_token = _env("TEST_ACCESS_TOKEN")
        if explicit_token:
            expiration = _jwt_expiration(explicit_token)
            if expiration is None or expiration > self._now() + _REFRESH_WINDOW_SECONDS:
                return explicit_token
            if not self._credentials_available(normalized_role):
                raise LoadTestAuthenticationError(
                    "TEST_ACCESS_TOKEN is expired or near expiry and no load-test credentials are configured."
                )

        with self._lock:
            session = self._sessions.get(normalized_role)
            if session is None:
                session = self._login(normalized_role)
            elif session.expires_at <= self._now() + _REFRESH_WINDOW_SECONDS:
                try:
                    session = self._refresh(session)
                except LoadTestAuthenticationError:
                    session = self._login(normalized_role)
            self._sessions[normalized_role] = session
            return session.access_token

    def configured(self, role: str | None = None) -> bool:
        normalized_role = (role or "generic").strip().lower()
        return bool(_env("TEST_ACCESS_TOKEN")) or self._credentials_available(normalized_role)

    def _credentials_available(self, role: str) -> bool:
        role_prefix = f"LOAD_TEST_{role.upper()}" if role != "generic" else ""
        role_email = _env(f"{role_prefix}_EMAIL") if role_prefix else ""
        role_password = _env(f"{role_prefix}_PASSWORD") if role_prefix else ""
        if role_email or role_password:
            return bool(role_email and role_password)
        return bool(_env("LOAD_TEST_EMAIL") and _env("LOAD_TEST_PASSWORD"))

    def _credentials(self, role: str) -> tuple[str, str]:
        role_prefix = f"LOAD_TEST_{role.upper()}" if role != "generic" else ""
        role_email = _env(f"{role_prefix}_EMAIL") if role_prefix else ""
        role_password = _env(f"{role_prefix}_PASSWORD") if role_prefix else ""
        if role_email or role_password:
            if not role_email or not role_password:
                raise LoadTestAuthenticationError(
                    f"LOAD_TEST_{role.upper()}_EMAIL and LOAD_TEST_{role.upper()}_PASSWORD must both be set."
                )
            return role_email, role_password

        email = _env("LOAD_TEST_EMAIL")
        password = _env("LOAD_TEST_PASSWORD")
        if not email or not password:
            raise LoadTestAuthenticationError(
                f"{role.capitalize()} authentication requires TEST_ACCESS_TOKEN or load-test email/password credentials."
            )
        return email, password

    def _auth_configuration(self) -> tuple[str, str]:
        url = _env("SUPABASE_URL").rstrip("/")
        publishable_key = _env("SUPABASE_PUBLISHABLE_KEY")
        if not url or not publishable_key:
            raise LoadTestAuthenticationError(
                "Automatic load-test login requires SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY."
            )
        return url, publishable_key

    def _login(self, role: str) -> _Session:
        email, password = self._credentials(role)
        url, publishable_key = self._auth_configuration()
        response = self._request(
            f"{url}/auth/v1/token?grant_type=password",
            publishable_key,
            {"email": email, "password": password},
            action="login",
        )
        return self._session_from_response(response)

    def _refresh(self, session: _Session) -> _Session:
        url, publishable_key = self._auth_configuration()
        response = self._request(
            f"{url}/auth/v1/token?grant_type=refresh_token",
            publishable_key,
            {"refresh_token": session.refresh_token},
            action="refresh",
        )
        return self._session_from_response(response)

    def _request(self, url: str, publishable_key: str, payload: dict[str, str], *, action: str) -> dict:
        try:
            response = self._post(
                url,
                headers={"apikey": publishable_key, "Content-Type": "application/json"},
                json=payload,
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise LoadTestAuthenticationError(f"Supabase load-test {action} request failed.") from exc
        if not response.is_success:
            raise LoadTestAuthenticationError(
                f"Supabase load-test {action} failed with HTTP {response.status_code}."
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise LoadTestAuthenticationError(f"Supabase load-test {action} returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise LoadTestAuthenticationError(f"Supabase load-test {action} returned an invalid response.")
        return result

    def _session_from_response(self, payload: dict) -> _Session:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise LoadTestAuthenticationError("Supabase authentication response did not include an access token.")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise LoadTestAuthenticationError("Supabase authentication response did not include a refresh token.")
        expires_at = payload.get("expires_at")
        if expires_at is None:
            expires_at = self._now() + float(payload.get("expires_in", 3600))
        return _Session(access_token=access_token, refresh_token=refresh_token, expires_at=float(expires_at))


_AUTH = LoadTestAuth()


def bearer_headers(role: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_AUTH.access_token(role)}",
        "Content-Type": "application/json",
    }


def authentication_configured(role: str | None = None) -> bool:
    return _AUTH.configured(role)


def reset_auth_cache() -> None:
    """Clear only in-memory sessions; intended for tests and fresh harness runs."""
    _AUTH.clear()
