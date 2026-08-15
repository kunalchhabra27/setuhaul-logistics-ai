"""AWS Bedrock AgentCore entrypoint for the SetuHaul driver chatbot's LLM path.

This is the real agent code (not the `agentcore create` starter template) --
it imports DriverChatService/DriverChatRepository/the LangChain tool-calling
agent from the vendored `setuhaul/` package below (a copy of the monorepo's
`src/setuhaul`, since AgentCore's Container build only sees this directory as
its Docker build context -- see "Keeping setuhaul in sync" in
../../README.md at the repo root, or agentcore_app/README.md, for the copy
command to rerun whenever src/setuhaul changes).

It does not reimplement booking/ETA/dock logic -- it's a second, much
smaller front door onto the same package the Vercel-hosted FastAPI app uses.
Talks to Supabase directly using the driver's own forwarded JWT (the same
caller-scoped, RLS-respecting client the rest of the app already uses).

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

# Auto-instruments every LangChain/ChatGoogleGenerativeAI call for
# CloudWatch GenAI Observability -- must run before any langchain import
# actually builds a chain/LLM client. The Dockerfile also wraps the process
# with `opentelemetry-instrument`, which is what ships these spans to
# CloudWatch once Transaction Search is enabled (see DEPLOYMENT_PLAN.md §2.7).
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

LangchainInstrumentor().instrument()

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
