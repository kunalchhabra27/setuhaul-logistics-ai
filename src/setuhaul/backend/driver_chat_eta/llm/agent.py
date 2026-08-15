"""Open-weights tool-calling conversational agent for driver_chat_eta.

Architecture
------------
Free-text driver messages are handled by a small tool-calling loop: a
Pydantic schema per tool, ``ChatHuggingFace(...).bind_tools(...)``, and a
loop that keeps invoking tools and feeding their JSON results back to the
model until it stops requesting tool calls. The model itself is served via
Hugging Face Inference Providers (``HuggingFaceEndpoint`` with
``provider="auto"`` by default, see ``settings.driver_chat_llm_provider``)
rather than a locally-hosted model -- ``ChatHuggingFace`` talks to whichever
provider (Together, Fireworks, Novita, Cerebras, ...) is currently hosting
``settings.driver_chat_llm_model`` over an OpenAI-compatible chat-completions
API that supports the same ``tools``/``tool_choice`` semantics this loop
already relies on.

- ``driver_chat_eta.llm.schemas`` -- Pydantic input schema per tool (what
  the model is allowed to fill in for a tool call).
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

This module is only imported (and ``ChatHuggingFace``/``redis`` are only
imported inside functions, not at module scope) when
``service.handle_chat_message`` decides an LLM turn is possible -- see
``is_configured()``. That keeps the regex fallback in ``service.py`` fully
usable even in environments where these optional dependencies aren't
installed.

Open models are generally weaker than frontier hosted models (Claude,
Gemini, GPT) at strict, CONDITIONAL tool-calling -- reliably calling
book_next_available_dock_slot only when actually asked to book/change a
slot, and answering everything else in plain text with no tool call at all.
``llm/prompts.py``'s rules 1 and 3 exist specifically to keep that behavior
in check; if this model starts over-triggering tools again, that prompt is
the first place to look, and swapping ``DRIVER_CHAT_LLM_MODEL`` to a larger
model (or a different provider) is the next lever.

Voice-note transcription (``transcribe_audio`` below) is the one exception
to "everything runs on the HF-hosted model": there's no equivalent native
audio-input path through HF's routed chat-completions API, so that one call
still goes to Gemini (see ``transcription_is_configured()``, gated
independently of the main chat agent by ``GOOGLE_API_KEY`` rather than
``HUGGINGFACEHUB_API_TOKEN``). The resulting transcript is plain text by the
time it reaches the tool-calling loop above, so this split is invisible to
everything else in the pipeline.
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
from setuhaul.infrastructure.metrics import Duration, emit_domain_event, increment
from setuhaul.infrastructure.telemetry import (
    langsmith_trace_context,
    operation_span,
    set_current_langsmith_metadata,
    set_current_span_attributes,
)

if TYPE_CHECKING:
    from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
    from setuhaul.backend.driver_chat_eta.service import DriverChatService

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
HISTORY_HYDRATE_LIMIT = 20

# Tools that actually change dock/exception/checkin state -- only these
# warrant recomputing a fresh snapshot (which includes a full dock-slot
# feasibility pass, see DriverChatService._feasible_slots) after the turn.
# report_delay_or_eta_change, book_next_available_dock_slot, and
# update_arrival_checkin all mutate something the snapshot reflects;
# list_feasible_dock_slots and escalate_to_human don't (the former already
# returns fresh slots straight from its own tool result; the latter is
# folded into book_next_available_dock_slot's own state change when it
# triggers automatically, and is otherwise a thread-status-only change that
# doesn't affect dock-slot feasibility).
STATE_CHANGING_TOOLS = {
    "report_delay_or_eta_change",
    "book_next_available_dock_slot",
    "update_arrival_checkin",
}


def is_configured() -> bool:
    """Whether HUGGINGFACEHUB_API_TOKEN is set, i.e. whether the main
    HF-hosted tool-calling chat agent should be tried. Does NOT gate
    voice-note transcription -- see transcription_is_configured() for that,
    since the two now depend on different API keys/providers."""
    return bool(get_settings().huggingface_api_token)


def transcription_is_configured() -> bool:
    """Whether GOOGLE_API_KEY is set, i.e. whether transcribe_audio() can
    run. Voice notes are a separate, optional capability from the main chat
    agent (gated by is_configured()/HUGGINGFACEHUB_API_TOKEN above) -- a
    deployment can have one key, both, or neither; an unset key here just
    means the driver has to type instead of using the mic, it never fails
    the main chat path."""
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

    Still Gemini, not the HF-hosted model -- see the module docstring on why
    this one call is the exception to "everything runs on the open model"
    above.
    """
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=settings.driver_chat_transcription_model,
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
    """Run the existing LLM flow under optional, driver-chat-only tracing."""
    increment("setuhaul.ai.calls", {"operation": "agent_run"})
    emit_domain_event("agent_invoked", operation="agent_run")
    try:
        with Duration("ai", {"operation": "agent_run"}):
            with operation_span("driver_chat.agent_execution", {"operation": "agent_execution"}):
                with langsmith_trace_context({"operation": "driver_chat"}):
                    return _run_chat_turn(service, principal, text)
    except Exception:
        increment("setuhaul.ai.errors", {"operation": "agent_run"})
        emit_domain_event("agent_failed", operation="agent_run", result="error")
        raise


def _run_chat_turn(service: "DriverChatService", principal: "DriverPrincipal", text: str) -> ChatResponse:
    """Handle one driver chat message with the HF-hosted tool-calling agent."""
    with operation_span("driver_chat.load_driver_profile", {"operation": "load_driver_profile"}):
        driver = service.get_my_profile(principal)  # raises DriverProfileNotFoundError if missing
    with operation_span("driver_chat.load_snapshot", {"operation": "load_snapshot"}):
        snapshot = service.snapshot(principal)

    if snapshot.shipment is None:
        return _no_shipment_reply(driver, snapshot, text)

    with operation_span("driver_chat.load_thread_context", {"operation": "load_thread_context"}):
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
    safe_context = {
        "shipment_id": snapshot.shipment.shipment_id,
        "thread_id": thread_id,
        "exception_id": snapshot.exception.exception_id if snapshot.exception else None,
        "environment": get_settings().environment,
    }
    set_current_span_attributes(safe_context)
    set_current_langsmith_metadata(safe_context)

    # Hydrate working memory before writing today's message, so it isn't duplicated.
    with operation_span("driver_chat.load_conversation_history", {"operation": "load_conversation_history"}):
        history = load_history(principal.user_id, thread_id)
        if not history:
            history = _hydrate_from_persisted(service, thread_id)

    with operation_span("driver_chat.persist_driver_message", {"operation": "persist_driver_message"}):
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
    from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

    settings = get_settings()
    tools = build_tools(service, principal)
    tool_map = {t.name: t for t in tools}
    # temperature=0 is rejected/undefined behavior on some HF-routed
    # providers (a holdover from TGI-style backends) -- 0.01 gets the same
    # near-deterministic behavior every other provider in this file uses
    # temperature=0 for, without risking that edge case.
    endpoint = HuggingFaceEndpoint(
        repo_id=settings.driver_chat_llm_model,
        provider=settings.driver_chat_llm_provider,
        huggingfacehub_api_token=settings.huggingface_api_token,
        temperature=0.01,
        max_new_tokens=1024,
    )
    llm = ChatHuggingFace(llm=endpoint)
    llm_with_tools = llm.bind_tools(tools)

    system_prompt = build_system_prompt(driver, snapshot)
    prompt = ChatPromptTemplate.from_messages(
        [SystemMessage(content=system_prompt), MessagesPlaceholder("messages")]
    )
    chain = prompt | llm_with_tools

    history = [*history, HumanMessage(content=text)]

    with operation_span("driver_chat.langchain", {"operation": "langchain"}):
        response = chain.invoke({"messages": history})
        history.append(response)

        rounds_used = 0
        called_tool_names: set[str] = set()
        while getattr(response, "tool_calls", None) and rounds_used < MAX_TOOL_ROUNDS:
            rounds_used += 1
            for call in response.tool_calls:
                called_tool_names.add(call["name"])
                increment("setuhaul.ai.tool_calls", {"operation": "agent_tool_call"})
                emit_domain_event("agent_tool_called", operation="agent_tool_call")
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

    with operation_span("driver_chat.persist_session", {"operation": "persist_session"}):
        save_history(principal.user_id, thread_id, history)

    reply_text = _extract_text(response.content) or (
        "Got it -- let me know if you need anything else."
    )
    with operation_span("driver_chat.persist_agent_message", {"operation": "persist_agent_message"}):
        agent_row = service.repository.insert_chat_message(
            {
                "chat_message_id": _new_id("MSG"),
                "thread_id": thread_id,
                "sender_type": "AGENT",
                "message_text": reply_text,
                "message_ts": _now_iso(),
            }
        )

    # _build_snapshot recomputes dock-slot feasibility from scratch (a full
    # board query) -- this loop already unconditionally did that once
    # before the LLM even ran. Only pay for it again if a tool that could
    # actually have changed shipment/exception/dock/checkin state was
    # called this turn; otherwise the original snapshot is still accurate
    # and re-fetching it was the literal cost behind "the chatbot keeps
    # checking dock status on every message" even for plain small talk.
    with operation_span("driver_chat.prepare_response", {"operation": "prepare_response"}):
        if called_tool_names & STATE_CHANGING_TOOLS:
            fresh_snapshot = service._build_snapshot(principal, driver)
        else:
            fresh_snapshot = snapshot
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
