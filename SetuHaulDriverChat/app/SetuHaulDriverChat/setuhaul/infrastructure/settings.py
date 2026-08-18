"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal runtimes
    BaseSettings = BaseModel

    class SettingsConfigDict(dict):  # type: ignore[override]
        """Fallback config container when pydantic-settings is unavailable."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

# get_settings() below builds its config dict entirely from os.getenv(...) and
# calls Settings.model_validate(data) -- NOT the Settings(...) constructor --
# so pydantic-settings' own `env_file=".env"` auto-loading (declared on
# model_config below) never actually runs; model_validate() skips
# BaseSettings' source-loading machinery entirely. Without this explicit
# load_dotenv() call, every os.getenv() in this module (and in main.py) only
# sees real OS/process environment variables, not values from .env -- which
# is exactly the "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY: Input should be a
# valid string [input_value=None]" crash you get if you launch uvicorn from a
# plain shell that hasn't sourced .env itself (an IDE run/debug config with
# an "env file" setting can mask this, since it exports the vars for you).
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(filename=".env", usecwd=True), override=False)
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal runtimes
    pass


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_publishable_key: str = Field(alias="SUPABASE_PUBLISHABLE_KEY")
    data_backend: str = Field(default="local", alias="DATA_BACKEND")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # driver_chat_eta LLM chatbot -- an open-weights model served via Hugging
    # Face Inference Providers, through langchain-huggingface's
    # ChatHuggingFace/HuggingFaceEndpoint (OpenAI-compatible tool-calling
    # chat completions, routed to whichever provider actually hosts the
    # model). huggingface_api_token is intentionally optional -- when unset,
    # driver_chat_eta.service falls back to its deterministic regex-based
    # chat parser instead of failing (see llm.agent.is_configured()).
    huggingface_api_token: str | None = Field(default=None, alias="HUGGINGFACEHUB_API_TOKEN")
    # repo_id of the model on the Hub. Llama 3.3 70B Instruct is used as the
    # default because it's one of the strongest open models at strict,
    # conditional tool-calling (the exact skill this agent leans on --
    # calling book_next_available_dock_slot only when actually asked to, not
    # as a catch-all) and is broadly available across HF's routed providers.
    driver_chat_llm_model: str = Field(
        default="meta-llama/Llama-3.3-70B-Instruct", alias="DRIVER_CHAT_LLM_MODEL"
    )
    # Which HF Inference Provider actually serves the model (Together,
    # Fireworks, Novita, Cerebras, etc.) -- "auto" lets HF route to the
    # fastest available one for this repo_id, so this rarely needs changing.
    # See https://huggingface.co/settings/inference-providers.
    driver_chat_llm_provider: str = Field(default="auto", alias="DRIVER_CHAT_LLM_PROVIDER")
    # Voice-note transcription ONLY (llm.agent.transcribe_audio) -- kept on
    # Gemini's native multimodal audio input, which the open models above
    # don't have an equivalent for via HF's routed chat-completions API.
    # Also optional: llm.agent.transcription_is_configured() gates the
    # voice-message endpoint independently of huggingface_api_token above,
    # so an unset key just disables voice notes (the driver can still type)
    # instead of failing the request.
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    driver_chat_transcription_model: str = Field(
        default="gemini-2.5-flash", alias="DRIVER_CHAT_TRANSCRIPTION_MODEL"
    )
    # Optional hot session cache for the LLM agent's tool-call scratchpad.
    # When unset, the agent reconstructs working memory from the permanent
    # chat_messages table in Supabase instead (slower, coarser, but never
    # loses data).
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    # Optional observability harness configuration. These defaults keep local
    # development and all existing flows unchanged unless explicitly enabled.
    otel_enabled: bool = Field(default=False, alias="OTEL_ENABLED")
    otel_exporter_otlp_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_exporter_otlp_protocol: str = Field(default="http/protobuf", alias="OTEL_EXPORTER_OTLP_PROTOCOL")
    otel_service_name: str = Field(default="setuhaul-backend", alias="OTEL_SERVICE_NAME")
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="setuhaul-harness", alias="LANGSMITH_PROJECT")

    # Twilio SMS notifications (check-in confirmation, driver assignment).
    # All three are optional -- when unset, setuhaul.infrastructure.sms simply
    # skips sending and logs instead of failing the request. Never hardcode
    # real values here; set them in your own untracked .env file.
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    twilio_from_number: str | None = Field(default=None, alias="TWILIO_FROM_NUMBER")

    # AWS Bedrock AgentCore -- when agentcore_runtime_arn is set,
    # driver_chat_eta.service.handle_chat_message routes free-text driver
    # messages to the AgentCore-hosted LLM agent (infrastructure/
    # agentcore_client.py) instead of running Gemini in-process. When unset
    # (e.g. local dev without AWS credentials), it falls back to the
    # original in-process llm.agent.run_chat_turn path, then to the regex
    # parser -- see DEPLOYMENT_PLAN.md for the full architecture. AWS
    # credentials themselves are never read here: boto3 picks those up from
    # its own standard chain (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env
    # vars, ~/.aws/credentials, or an assumed role), not from this Settings
    # model.
    agentcore_runtime_arn: str | None = Field(default=None, alias="AGENTCORE_RUNTIME_ARN")
    # Seconds to wait for one AgentCore invocation before giving up and
    # falling back to the regex parser. Gemini calls have taken 20-40s+
    # under free-tier quota pressure in testing -- keep this generous, but
    # bounded, so a hung AWS call can't hang a driver's whole chat turn
    # indefinitely.
    agentcore_invoke_timeout_seconds: int = Field(default=45, alias="AGENTCORE_INVOKE_TIMEOUT_SECONDS")

    @field_validator("supabase_url")
    @classmethod
    def normalize_supabase_url(cls, value: str) -> str:
        """Remove trailing slashes so client paths remain stable."""
        return value.rstrip("/")

    @field_validator("data_backend")
    @classmethod
    def normalize_data_backend(cls, value: str) -> str:
        return value.strip().lower()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    if hasattr(Settings, "model_validate"):
        data = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_PUBLISHABLE_KEY": os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            "DATA_BACKEND": os.getenv("DATA_BACKEND", "local"),
            "ENVIRONMENT": os.getenv("ENVIRONMENT", "development"),
            "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
            "HUGGINGFACEHUB_API_TOKEN": os.getenv("HUGGINGFACEHUB_API_TOKEN"),
            "DRIVER_CHAT_LLM_MODEL": os.getenv("DRIVER_CHAT_LLM_MODEL", "meta-llama/Llama-3.3-70B-Instruct"),
            "DRIVER_CHAT_LLM_PROVIDER": os.getenv("DRIVER_CHAT_LLM_PROVIDER", "auto"),
            "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY"),
            "DRIVER_CHAT_TRANSCRIPTION_MODEL": os.getenv("DRIVER_CHAT_TRANSCRIPTION_MODEL", "gemini-2.5-flash"),
            "REDIS_URL": os.getenv("REDIS_URL"),
            "OTEL_ENABLED": os.getenv("OTEL_ENABLED", "false"),
            "OTEL_EXPORTER_OTLP_ENDPOINT": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
            "OTEL_EXPORTER_OTLP_PROTOCOL": os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
            "OTEL_SERVICE_NAME": os.getenv("OTEL_SERVICE_NAME", "setuhaul-backend"),
            "AWS_REGION": os.getenv("AWS_REGION"),
            "LANGSMITH_TRACING": os.getenv("LANGSMITH_TRACING", "false"),
            "LANGSMITH_API_KEY": os.getenv("LANGSMITH_API_KEY"),
            "LANGSMITH_PROJECT": os.getenv("LANGSMITH_PROJECT", "setuhaul-harness"),
            "TWILIO_ACCOUNT_SID": os.getenv("TWILIO_ACCOUNT_SID"),
            "TWILIO_AUTH_TOKEN": os.getenv("TWILIO_AUTH_TOKEN"),
            "TWILIO_FROM_NUMBER": os.getenv("TWILIO_FROM_NUMBER"),
            "AGENTCORE_RUNTIME_ARN": os.getenv("AGENTCORE_RUNTIME_ARN"),
            "AGENTCORE_INVOKE_TIMEOUT_SECONDS": os.getenv("AGENTCORE_INVOKE_TIMEOUT_SECONDS", "45"),
        }
        return Settings.model_validate(data)  # type: ignore[attr-defined]
    return Settings(
        SUPABASE_URL=os.getenv("SUPABASE_URL"),
        SUPABASE_PUBLISHABLE_KEY=os.getenv("SUPABASE_PUBLISHABLE_KEY"),
        DATA_BACKEND=os.getenv("DATA_BACKEND", "local"),
        ENVIRONMENT=os.getenv("ENVIRONMENT", "development"),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        HUGGINGFACEHUB_API_TOKEN=os.getenv("HUGGINGFACEHUB_API_TOKEN"),
        DRIVER_CHAT_LLM_MODEL=os.getenv("DRIVER_CHAT_LLM_MODEL", "meta-llama/Llama-3.3-70B-Instruct"),
        DRIVER_CHAT_LLM_PROVIDER=os.getenv("DRIVER_CHAT_LLM_PROVIDER", "auto"),
        GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY"),
        DRIVER_CHAT_TRANSCRIPTION_MODEL=os.getenv("DRIVER_CHAT_TRANSCRIPTION_MODEL", "gemini-2.5-flash"),
        REDIS_URL=os.getenv("REDIS_URL"),
        OTEL_ENABLED=os.getenv("OTEL_ENABLED", "false"),
        OTEL_EXPORTER_OTLP_ENDPOINT=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        OTEL_EXPORTER_OTLP_PROTOCOL=os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
        OTEL_SERVICE_NAME=os.getenv("OTEL_SERVICE_NAME", "setuhaul-backend"),
        AWS_REGION=os.getenv("AWS_REGION"),
        LANGSMITH_TRACING=os.getenv("LANGSMITH_TRACING", "false"),
        LANGSMITH_API_KEY=os.getenv("LANGSMITH_API_KEY"),
        LANGSMITH_PROJECT=os.getenv("LANGSMITH_PROJECT", "setuhaul-harness"),
        TWILIO_ACCOUNT_SID=os.getenv("TWILIO_ACCOUNT_SID"),
        TWILIO_AUTH_TOKEN=os.getenv("TWILIO_AUTH_TOKEN"),
        TWILIO_FROM_NUMBER=os.getenv("TWILIO_FROM_NUMBER"),
        AGENTCORE_RUNTIME_ARN=os.getenv("AGENTCORE_RUNTIME_ARN"),
        AGENTCORE_INVOKE_TIMEOUT_SECONDS=os.getenv("AGENTCORE_INVOKE_TIMEOUT_SECONDS", "45"),
    )
