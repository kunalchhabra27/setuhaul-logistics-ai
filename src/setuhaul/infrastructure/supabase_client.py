"""Supabase client factories for caller-scoped Data API access."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from setuhaul.infrastructure.settings import Settings

try:  # pragma: no cover - import-time compatibility shim
    from supabase import ClientOptions, create_client
except ModuleNotFoundError:  # pragma: no cover - minimal-runtime fallback
    ClientOptions = Any  # type: ignore[assignment]

    def create_client(*_: Any, **__: Any) -> Any:
        raise ModuleNotFoundError("supabase package is not available in this Python environment")

if TYPE_CHECKING:
    from supabase import Client
else:
    Client = Any


def create_public_client(settings: Settings) -> Client:
    """Create a stateless client using only the publishable project key."""
    return create_client(
        settings.supabase_url,
        settings.supabase_publishable_key,
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


def create_caller_client(settings: Settings, access_token: str) -> Client:
    """Create a client whose PostgREST requests carry the caller JWT."""
    client = create_public_client(settings)
    client.postgrest.auth(access_token)
    return client
