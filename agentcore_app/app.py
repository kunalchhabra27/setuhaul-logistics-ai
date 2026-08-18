"""AWS Bedrock AgentCore entrypoint for the driver chatbot's LLM path.

This is a second, much smaller front door onto the SAME setuhaul package the
Vercel-hosted FastAPI app uses -- it is not a reimplementation of booking
logic. It imports DriverChatService/DriverChatRepository/the LangChain
tool-calling agent exactly as service.handle_chat_message does today, and
talks to Supabase directly using the driver's own forwarded JWT (same
caller-scoped, RLS-respecting client the rest of the app already uses via
setuhaul.infrastructure.supabase_client.create_caller_client).

Runs as a standalone container: BedrockAgentCoreApp serves its own
/invocations and /ping endpoints on port 8080 -- there is no FastAPI/uvicorn
here, and this file must not import setuhaul.main or anything that pulls in
the tms/dock_scheduler/checkin_portal REST routers.

Invocation payload shape (see infrastructure/agentcore_client.py on the
Vercel/FastAPI side for the caller):
    {
        "driver_jwt": "<the driver's Supabase access token>",
        "driver_id": "<Supabase auth user id>",
        "driver_email": "<optional>",
        "message": "<the driver's free-text chat message>",
    }

Returns the same JSON shape as ChatResponse.model_dump(mode="json") --
the caller re-validates it back into a ChatResponse with
ChatResponse.model_validate(...).
"""

from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.backend.driver_chat_eta.exceptions import DriverChatError
from setuhaul.backend.driver_chat_eta.llm import agent as llm_agent
from setuhaul.backend.driver_chat_eta.repository import DriverChatRepository
from setuhaul.backend.driver_chat_eta.service import DriverChatService
from setuhaul.infrastructure.settings import get_settings
from setuhaul.infrastructure.supabase_client import create_caller_client

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:  # noqa: ARG001 - context unused, part of the AgentCore signature
    driver_jwt = payload["driver_jwt"]
    driver_id = payload["driver_id"]
    message = payload["message"]

    settings = get_settings()
    client = create_caller_client(settings, driver_jwt)
    service = DriverChatService(DriverChatRepository(client))
    principal = DriverPrincipal(
        user_id=driver_id,
        email=payload.get("driver_email"),
        access_token=driver_jwt,
    )

    try:
        # Same LLM tool-calling loop service.handle_chat_message runs
        # in-process today -- deliberately calling it directly rather than
        # going through handle_chat_message's is_configured()/try-except,
        # since this container's whole job IS the LLM path. If Gemini
        # itself fails mid-turn, fall back to the same deterministic regex
        # parser the rest of the app uses, so a driver never gets a hard
        # error just because the LLM had a bad moment.
        response = llm_agent.run_chat_turn(service, principal, message)
    except DriverChatError:
        raise
    except Exception:  # noqa: BLE001 - deliberate broad fallback boundary, mirrors service.handle_chat_message
        logger.exception("agentcore: LLM chat agent failed, falling back to regex parser.")
        response = service._handle_chat_message_regex(principal, message)  # noqa: SLF001

    return response.model_dump(mode="json")


if __name__ == "__main__":
    app.run()
