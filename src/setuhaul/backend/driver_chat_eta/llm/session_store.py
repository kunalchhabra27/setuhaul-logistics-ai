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

Connection building (standalone vs. Redis Cluster, retry/timeout policy,
the singleton/cooldown lifecycle) is shared with ``infrastructure.cache``
via ``infrastructure.redis_client`` -- one connection/pool per process for
the whole app, not a second independent one. This module keeps its own
storage semantics (key scheme, TTL, LangChain message
serialization) entirely separate from cache.py's cache-aside layer; it just
doesn't build its own client anymore. Its key
(``chat:setuhaul:{driver_id}:{thread_id}``, an ordinary single-key string,
not a Redis Cluster hash tag despite the brace-looking f-string
placeholders) is never part of a multi-key operation, so unlike cache.py it
needs no hash-tag scheme -- see ``infrastructure.cache``'s module docstring
for why that module needs one and this one doesn't.
"""

from __future__ import annotations

import json
import logging

from setuhaul.infrastructure import redis_client

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 1800


def _redis_key(driver_id: str, thread_id: str) -> str:
    return f"chat:setuhaul:{driver_id}:{thread_id}"


def load_history(driver_id: str, thread_id: str) -> list:
    """Return the cached LangChain message list for this driver+thread, or []."""
    client = redis_client.get_client()
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
    client = redis_client.get_client()
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
