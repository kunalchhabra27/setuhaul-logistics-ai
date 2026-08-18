"""boto3 wrapper for invoking the AWS Bedrock AgentCore-hosted driver chatbot.

Used only by driver_chat_eta.service.handle_chat_message, and only when
Settings.agentcore_runtime_arn is set (see settings.py) -- i.e. in
production, once agentcore_app/ has been deployed per DEPLOYMENT_PLAN.md.
Local dev without AWS credentials configured simply never sets
AGENTCORE_RUNTIME_ARN, so this module is never invoked and the existing
in-process llm.agent.run_chat_turn path (or the regex fallback) is used
instead -- see handle_chat_message's three-way fallback chain.

boto3 itself is never given credentials directly here; it uses its own
standard resolution chain (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars,
~/.aws/credentials, or an assumed role), matching how `aws configure`/CI
secrets are expected to be wired up wherever this runs (Vercel, in this
app's case).
"""

from __future__ import annotations

import json
import logging
import uuid

from setuhaul.backend.driver_chat_eta.auth import DriverPrincipal
from setuhaul.backend.driver_chat_eta.exceptions import DriverChatError
from setuhaul.backend.driver_chat_eta.models import ChatResponse
from setuhaul.infrastructure.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class AgentCoreUnavailableError(DriverChatError):
    """Raised when the AgentCore runtime call itself fails (network, timeout,
    throttling, malformed response) -- distinct from a DriverChatError the
    agent's own tool-calling loop raised deliberately (auth, validation),
    which is re-raised as-is instead of wrapped. Callers should catch this
    specifically to fall back to the regex parser, exactly like a local
    Gemini failure already does today.
    """

    status_code = 502
    code = "AGENTCORE_UNAVAILABLE"


def is_configured(settings: Settings | None = None) -> bool:
    """Whether AGENTCORE_RUNTIME_ARN is set, i.e. whether chat should be
    routed to AWS instead of the in-process LLM path."""
    settings = settings or get_settings()
    return bool(settings.agentcore_runtime_arn)


def invoke_driver_chat_agent(principal: DriverPrincipal, text: str) -> ChatResponse:
    """Call the AgentCore-hosted driver_chat_eta LLM agent and parse its
    response back into a ChatResponse -- the same object
    llm.agent.run_chat_turn returns when run in-process, since the
    AgentCore container returns response.model_dump(mode="json") of that
    exact same pydantic model (see agentcore_app/app.py).
    """
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    settings = get_settings()
    if not settings.agentcore_runtime_arn:
        raise AgentCoreUnavailableError("AGENTCORE_RUNTIME_ARN is not configured.")

    client = boto3.client(
        "bedrock-agentcore",
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=10,
            read_timeout=settings.agentcore_invoke_timeout_seconds,
            retries={"max_attempts": 1},  # a single retry loop belongs in the caller's fallback-to-regex path, not here
        ),
    )

    payload = json.dumps(
        {
            "driver_jwt": principal.access_token,
            "driver_id": principal.user_id,
            "driver_email": principal.email,
            "message": text,
        }
    ).encode("utf-8")

    # runtimeSessionId must be >= 33 characters; deriving it deterministically
    # from the driver's own id (rather than a fresh uuid4 per call) means
    # every turn from the same driver lands on the same session id, which
    # helps AgentCore route repeat calls to a warm session/container instead
    # of a cold one. This is independent from -- not a replacement for --
    # driver_chat_eta's own thread_id/session_store.py working-memory
    # tracking, which still lives inside run_chat_turn on the AWS side.
    runtime_session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"setuhaul-driver-{principal.user_id}"))

    try:
        result = client.invoke_agent_runtime(
            agentRuntimeArn=settings.agentcore_runtime_arn,
            runtimeSessionId=runtime_session_id,
            payload=payload,
        )
        body = result["response"].read()
        data = json.loads(body)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("agentcore_client: invoke_agent_runtime failed, caller should fall back.", exc_info=True)
        raise AgentCoreUnavailableError(str(exc)) from exc
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("agentcore_client: malformed response from AgentCore runtime.", exc_info=True)
        raise AgentCoreUnavailableError(f"Malformed AgentCore response: {exc}") from exc

    # NOTE: agentcore_app/app.py re-raises DriverChatError (auth failures,
    # etc.) rather than swallowing it, so a deliberate typed error on the
    # AWS side surfaces as an AgentCore invocation fault here, not as a
    # normal 200 response body -- boto3 raises that as a ClientError,
    # already caught above. Anything that reaches this point is expected to
    # be a successful ChatResponse.model_dump(...) payload; if it isn't,
    # treat it as an unavailable-runtime condition (see below) rather than
    # guessing at some other wire shape -- this hasn't been exercised
    # against a real deployed runtime yet, so stay conservative here and
    # verify the exact error shape once agentcore_app/ is actually deployed
    # (see agentcore_app/README.md's local test loop).
    try:
        return ChatResponse.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - any validation failure here is a malformed upstream response
        logger.warning("agentcore_client: response failed ChatResponse validation.", exc_info=True)
        raise AgentCoreUnavailableError(f"Could not parse AgentCore response: {exc}") from exc
