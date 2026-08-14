"""Supabase client factories for caller-scoped Data API access.

Both factories below now reuse cached `supabase.Client` instances (and,
inside each one, the httpx connection pool it opens to Supabase) instead of
constructing a brand-new client -- a fresh httpx transport, meaning a fresh
TCP+TLS handshake to Supabase -- on every single API call. Before this,
every portal's FastAPI dependency chain (`auth.get_current_*` +
`api.get_service`/equivalent, in driver_chat_eta, tms, checkin_portal, and
dock_scheduler) called `create_caller_client`/`create_public_client` fresh
on every request, which is the literal "multiple sessions with Supabase"
behavior flagged as a scaling concern for the 100-concurrent-driver chatbot
load test, and a real contributor to the drivers-portal "data takes a while
to show up after login" latency (each of the ~7-9 sequential Supabase calls
`_build_snapshot` makes was paying full connection-setup cost on top of the
query itself, every time, for every driver, on every request).

Two SEPARATE caches, deliberately not one shared client, because of a real,
documented footgun in supabase-py: passing one shared `httpx.Client` across
multiple Supabase sub-services (postgrest/auth/storage/realtime) causes each
sub-service to mutate that shared client's `base_url` on init, so requests
meant for one service can silently hit another service's endpoint (see
https://github.com/supabase/supabase-py/issues/1244). Nothing here shares a
single httpx.Client across services -- each cached `supabase.Client` still
builds its own independent sub-clients internally, exactly as
`create_client(...)` always has; the only change is REUSING the same
already-built `Client` object across requests instead of throwing it away
after one call.

- `create_public_client` -- always the same publishable-key-only client
  regardless of caller, so this is one process-wide singleton
  (`@lru_cache(maxsize=1)`). Nothing ever mutates its auth state (it's only
  ever used for stateless calls like `auth.get_user(token)`, which take the
  JWT as an explicit argument rather than persisting it on the client), so
  sharing this one instance across every request/thread is safe.
- `create_caller_client` -- carries a specific caller's JWT via
  `client.postgrest.auth(access_token)`, which DOES mutate that client's
  auth header. Reusing `create_public_client`'s singleton here would leak
  one driver's token onto another driver's requests, so this gets its own
  separate cache, keyed by the access token itself
  (`@lru_cache(maxsize=256)`) -- each distinct token gets its own client
  instance, reused only across that same caller's subsequent requests. The
  bound keeps memory flat even as many drivers log in/refresh tokens over a
  long-running process; a stale cached entry for an expired token isn't a
  correctness problem, since Supabase itself still rejects the request with
  401 exactly as a freshly-constructed client would (the frontend already
  retries once after a token refresh -- see frontend/src/services/api.ts).

`functools.lru_cache` is internally lock-protected, so this is safe under
FastAPI's sync-route threadpool without extra locking, and the underlying
httpx.Client each entry wraps is itself documented as safe for concurrent
requests from multiple threads.
"""

from __future__ import annotations

from functools import lru_cache
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


@lru_cache(maxsize=1)
def _cached_public_client(url: str, key: str) -> Client:
    return create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))


def create_public_client(settings: Settings) -> Client:
    """Reuse the one process-wide stateless client (publishable key only)."""
    return _cached_public_client(settings.supabase_url, settings.supabase_publishable_key)


@lru_cache(maxsize=256)
def _cached_caller_client(url: str, key: str, access_token: str) -> Client:
    client = create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))
    client.postgrest.auth(access_token)
    return client


def create_caller_client(settings: Settings, access_token: str) -> Client:
    """Reuse a client whose PostgREST requests carry the caller JWT, keyed by that JWT."""
    return _cached_caller_client(settings.supabase_url, settings.supabase_publishable_key, access_token)
