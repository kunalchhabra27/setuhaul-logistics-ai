"""Gemini tool-calling conversational agent for driver_chat_eta.

Architecture
------------
Free-text driver messages are handled by a small tool-calling loop, in the
same shape as the LangChain + Gemini pattern: a Pydantic schema per tool,
``ChatGoogleGenerativeAI(...).bind_tools(...)``, and a loop that keeps
invoking tools and feeding their JSON results back to the model until it
stops requesting tool calls.

- ``driver_chat_eta.llm.schemas`` -- Pydantic input schema per tool (what
  Gemini is allowed to fill in for a tool call).
- ``driver_chat_eta.llm.tools`` -- the actual tools, each a thin wrapper
  around an existing ``DriverChatService`` method. The LLM never touches
  Supabase directly and never bypasses the caller-scoped, RLS-respecting
  service layer -- it can only do what a button in the UI could already do.
- ``driver_chat_eta.llm.prompts`` -- the system prompt, built fresh each
  turn from a live snapshot of the driver's shipment/exception/slot state
  (so the model is always grounded in the current database, not stale
  memory).
- ``driver_chat_eta.llm.session_store`` -- Redis-backed hot working memory
  (tool-call scratchpad) for the current thread, with a graceful fallback
  to reconstructing a coarser memory from the permanent ``chat_messages``
  table when Redis isn't configured or is unreachable.

This module is only imported (and ``ChatGoogleGenerativeAI``/``redis`` are
only imported inside functions, not at module scope) when
``service.handle_chat_message`` decides an LLM turn is possible -- see
``is_configured()``. That keeps the regex fallback in ``service.py`` fully
usable even in environments where these optional dependencies aren't
installed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from setuhaul.backend.driver_chat_eta.llm.prompts import build_system_prompt
from setuhaul.backend.driver_chat_eta.llm.session_store import load_history, save_history
from setuhaul.backend.driver_chat_eta.llm.tools import build_tools
from setuhaul.backend.driver_chat_eta.models import ChatMessageSummary, ChatResponse
from setuhaul.backend.driver_chat_eta.service import _new_id, _now_iso
from setuhaul.infrastructure.settings import get_settings

if TYPE_CHECKING:
    from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
    from setuhaul.backend.driver_chat_eta.service import DriverChatService

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
HISTORY_HYDRATE_LIMIT = 20


def is_configured() -> bool:
    """Whether GOOGLE_API_KEY is set, i.e. whether the LLM path should be tried."""
    return bool(get_settings().google_api_key)


def transcribe_audio(audio_base64: str, mime_type: str) -> str:
    """Transcribe a short voice note to plain text using Gemini.

    Deliberately a separate, tool-free call rather than folding audio
    straight into the tool-calling turn: this keeps the multimodal input
    isolated to one small, easy-to-reason-about function, and means the
    rest of the pipeline (persistence, tool loop, regex fallback, the
    no-shipment path) only ever has to deal with plain text -- a voice
    message becomes a regular chat message the instant it has a transcript.
    Raises on any failure; callers should catch and surface a clear error
    rather than let a raw SDK exception reach the driver.
    """
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.driver_chat_llm_model,
        google_api_key=settings.google_api_key,
        temperature=0,
    )
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    "Transcribe this voice message to plain text, exactly as spoken, in the "
                    "original language. Reply with ONLY the transcript -- no quotes, no "
                    "commentary, no translation."
                ),
            },
            {"type": "media", "mime_type": mime_type, "data": audio_base64},
        ]
    )
    response = llm.invoke([message])
    return _extract_text(response.content).strip()


def run_chat_turn(service: "DriverChatService", principal: "DriverPrincipal", text: str) -> ChatResponse:
    """Handle one driver chat message with the Gemini tool-calling agent."""
    driver = service.get_my_profile(principal)  # raises DriverProfileNotFoundError if missing
    snapshot = service.snapshot(principal)

    if snapshot.shipment is None:
        return _no_shipment_reply(driver, snapshot, text)

    thread_row = service.repository.get_open_thread_for_driver(principal.user_id)
    if thread_row is None:
        thread_row = service.repository.create_thread(
            {
                "thread_id": _new_id("TH"),
                "driver_id": principal.user_id,
                "shipment_id": snapshot.shipment.shipment_id,
                "opened_at": _now_iso(),
                "thread_status": "OPEN",
                "thread_intent": "GENERAL_QUESTION",
            }
        )
    thread_id = thread_row["thread_id"]

    # Hydrate working memory before writing today's message, so it isn't duplicated.
    history = load_history(principal.user_id, thread_id)
    if not history:
        history = _hydrate_from_persisted(service, thread_id)

    service.repository.insert_chat_message(
        {
            "chat_message_id": _new_id("MSG"),
            "thread_id": thread_id,
            "sender_type": "DRIVER",
            "sender_reference": principal.user_id,
            "message_text": text,
            "message_ts": _now_iso(),
        }
    )

    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    tools = build_tools(service, principal)
    tool_map = {t.name: t for t in tools}
    llm = ChatGoogleGenerativeAI(
        model=settings.driver_chat_llm_model,
        google_api_key=settings.google_api_key,
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(tools)

    system_prompt = build_system_prompt(driver, snapshot)
    prompt = ChatPromptTemplate.from_messages(
        [SystemMessage(content=system_prompt), MessagesPlaceholder("messages")]
    )
    chain = prompt | llm_with_tools

    history = [*history, HumanMessage(content=text)]

    response = chain.invoke({"messages": history})
    history.append(response)

    rounds_used = 0
    while getattr(response, "tool_calls", None) and rounds_used < MAX_TOOL_ROUNDS:
        rounds_used += 1
        for call in response.tool_calls:
            tool_fn = tool_map.get(call["name"])
            if tool_fn is None:
                result: dict = {"error": "unknown_tool", "message": f"No such tool: {call['name']}"}
            else:
                try:
                    result = tool_fn.invoke(call["args"])
                except Exception:  # noqa: BLE001 - a tool bug must not crash the whole turn
                    logger.exception("driver_chat_eta: tool %s raised unexpectedly", call["name"])
                    result = {"error": "tool_failed", "message": "That action failed unexpectedly."}
            history.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"], name=call["name"]))
        response = chain.invoke({"messages": history})
        history.append(response)

    if rounds_used >= MAX_TOOL_ROUNDS and getattr(response, "tool_calls", None):
        logger.warning("driver_chat_eta: LLM tool loop hit max rounds for thread %s", thread_id)

    save_history(principal.user_id, thread_id, history)

    reply_text = _extract_text(response.content) or (
        "Got it -- let me know if you need anything else."
    )
    agent_row = service.repository.insert_chat_message(
        {
            "chat_message_id": _new_id("MSG"),
            "thread_id": thread_id,
            "sender_type": "AGENT",
            "message_text": reply_text,
            "message_ts": _now_iso(),
        }
    )

    fresh_snapshot = service._build_snapshot(principal, driver)
    exception_row = service.repository.get_active_exception_for_driver(principal.user_id)
    from setuhaul.backend.driver_chat_eta.models import DriverExceptionSummary

    return ChatResponse(
        agent_message=ChatMessageSummary.model_validate(agent_row),
        suggested_options=fresh_snapshot.slot_options,
        exception=DriverExceptionSummary.model_validate(exception_row) if exception_row else None,
        snapshot=fresh_snapshot,
    )


def _no_shipment_reply(driver, snapshot, driver_text: str) -> ChatResponse:
    """No active shipment yet -- answer without a thread, without calling the LLM.

    There is nothing for a tool-calling agent to usefully do (every action
    tool requires a shipment), so this skips the API call entirely rather
    than spending a request just to have the model say the same thing.

    There's also no chat_threads row to persist to or reload from, so the
    driver's message and this reply are attached directly onto the returned
    snapshot (not written to Supabase) -- the frontend renders
    snapshot.chat_messages verbatim, so without this the exchange would
    silently vanish instead of showing up in the chat window.
    """
    reply_text = (
        f"Hi {driver.driver_name or 'there'}, you don't have an active shipment assigned yet. "
        "Once dispatch assigns you a load, I can help with delays, dock slots, and check-ins."
    )
    driver_msg = ChatMessageSummary(
        chat_message_id=_new_id("MSG"),
        thread_id=None,
        sender_type="DRIVER",
        sender_reference=None,
        message_text=driver_text,
        message_ts=_now_iso(),
    )
    ephemeral = ChatMessageSummary(
        chat_message_id=_new_id("MSG"),
        thread_id=None,
        sender_type="AGENT",
        sender_reference=None,
        message_text=reply_text,
        message_ts=_now_iso(),
    )
    snapshot.chat_messages = [driver_msg, ephemeral]
    return ChatResponse(agent_message=ephemeral, suggested_options=[], exception=None, snapshot=snapshot)


def _hydrate_from_persisted(service: "DriverChatService", thread_id: str) -> list:
    """Rebuild coarse working memory (text turns only) from Supabase when Redis has nothing."""
    from langchain_core.messages import AIMessage, HumanMessage

    rows = service.repository.list_chat_messages(thread_id, limit=HISTORY_HYDRATE_LIMIT)
    messages: list = []
    for row in rows:
        sender = row.get("sender_type")
        text = row.get("message_text") or ""
        if not text:
            continue
        if sender == "DRIVER":
            messages.append(HumanMessage(content=text))
        elif sender in ("AGENT", "SYSTEM", "OPERATIONS"):
            messages.append(AIMessage(content=text))
    return messages


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return str(content) if content else ""
