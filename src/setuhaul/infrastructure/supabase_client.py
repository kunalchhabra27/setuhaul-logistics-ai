"""Supabase client factories for caller-scoped Data API access."""

from __future__ import annotations

from supabase import Client, ClientOptions, create_client

from setuhaul.infrastructure.settings import Settings


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
