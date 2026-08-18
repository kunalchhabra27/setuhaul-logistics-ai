"""Hot conversational memory for the driver chat LLM agent.

Redis holds the working message list (including intermediate tool-call and
tool-result turns) for a bounded TTL, isolated per driver+thread -- the
same pattern as `chat:{tenant}:{customer}:{thread}` session keys: never one
shared global key, never mixing one driver's working memory into another's.

This is a cache, not the source of truth. The permanent, RLS-scoped audit
trail is the ``chat_messages`` table in Supabase, written on every turn by
``agent.py`` regardless of whether Redis is configured. So nothing is lost
if Redis is unavailable or a session expires -- only the LLM's fine-grained
tool-call scratchpad is lost, and ``agent.py`` reconstructs a coarser
working memory (driver/agent text turns only, no tool-call detail) from
``chat_messages`` instead.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from setuhaul.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 1800


def _redis_key(driver_id: str, thread_id: str) -> str:
    return f"chat:setuhaul:{driver_id}:{thread_id}"


@lru_cache
def _client():
    """Return a process-wide Redis client, or None if unavailable/unconfigured."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis

        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:  # noqa: BLE001 - best-effort cache, never fatal
        logger.warning(
            "driver_chat_eta: could not connect to REDIS_URL, continuing without session cache.",
            exc_info=True,
        )
        return None


def load_history(driver_id: str, thread_id: str) -> list:
    """Return the cached LangChain message list for this driver+thread, or []."""
    client = _client()
    if client is None:
        return []
    try:
        stored = client.get(_redis_key(driver_id, thread_id))
    except Exception:  # noqa: BLE001
        logger.warning("driver_chat_eta: Redis read failed, continuing without session cache.", exc_info=True)
        return []
    if not stored:
        return []
    from langchain_core.messages import messages_from_dict

    try:
        return messages_from_dict(json.loads(stored))
    except Exception:  # noqa: BLE001 - corrupt/incompatible cache entry
        logger.warning("driver_chat_eta: discarding unreadable session cache entry.", exc_info=True)
        return []


def save_history(driver_id: str, thread_id: str, messages: list) -> None:
    """Best-effort write-through; a failure here must never break the chat turn."""
    client = _client()
    if client is None:
        return
    from langchain_core.messages import messages_to_dict

    try:
        client.set(
            _redis_key(driver_id, thread_id),
            json.dumps(messages_to_dict(messages), default=str),
            ex=SESSION_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.warning("driver_chat_eta: Redis write failed, session cache not updated.", exc_info=True)
