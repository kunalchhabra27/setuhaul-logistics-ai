"""Amazon Bedrock AgentCore adapter for the existing SetuHaul Driver Chat."""

from __future__ import annotations

import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.security import HTTPAuthorizationCredentials

from setuhaul.backend.driver_chat_eta.api import get_service
from setuhaul.backend.driver_chat_eta.auth import get_current_driver
from setuhaul.infrastructure.logging import configure_logging
from setuhaul.infrastructure.telemetry import initialize_telemetry, observe_operation

load_dotenv()
configure_logging(os.getenv("LOG_LEVEL", "INFO"))

# Reuse SetuHaul's telemetry setup rather than introducing AgentCore-specific
# tracing. The FastAPI host is only the initialization target for that helper.
_telemetry_host = FastAPI()
initialize_telemetry(_telemetry_host)

app = BedrockAgentCoreApp()
log = app.logger


def _access_token() -> str:
    token = os.getenv("TEST_DRIVER_ACCESS_TOKEN") or os.getenv("TEST_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "A driver bearer token is required in TEST_DRIVER_ACCESS_TOKEN or TEST_ACCESS_TOKEN."
        )
    return token


def _run_existing_driver_chat(prompt: str):
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_access_token())
    principal = get_current_driver(credentials)
    service = get_service(principal)
    return observe_operation(
        "driver_chat.request",
        {"operation": "chat_request"},
        lambda: service.handle_chat_message(principal, prompt),
    )


@app.entrypoint
def invoke(payload: dict[str, Any], context: Any) -> dict[str, Any]:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("The AgentCore payload must include a non-empty prompt.")

    response = _run_existing_driver_chat(prompt.strip())
    shipment = response.snapshot.shipment
    log.info("SetuHaul Driver Chat request completed through AgentCore.")
    return {
        "result": response.agent_message.message_text or "",
        "shipment_id": shipment.shipment_id if shipment else None,
        "thread_id": response.agent_message.thread_id,
    }


if __name__ == "__main__":
    app.run()
