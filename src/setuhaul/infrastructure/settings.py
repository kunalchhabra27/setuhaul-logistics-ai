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

    # driver_chat_eta LLM chatbot (Gemini tool-calling). google_api_key is
    # intentionally optional -- when unset, driver_chat_eta.service falls
    # back to its deterministic regex-based chat parser instead of failing.
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    driver_chat_llm_model: str = Field(default="gemini-2.5-flash", alias="DRIVER_CHAT_LLM_MODEL")
    # Optional hot session cache for the LLM agent's tool-call scratchpad.
    # When unset, the agent reconstructs working memory from the permanent
    # chat_messages table in Supabase instead (slower, coarser, but never
    # loses data).
    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    # Twilio SMS notifications (check-in confirmation, driver assignment).
    # All three are optional -- when unset, setuhaul.infrastructure.sms simply
    # skips sending and logs instead of failing the request. Never hardcode
    # real values here; set them in your own untracked .env file.
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    twilio_from_number: str | None = Field(default=None, alias="TWILIO_FROM_NUMBER")

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
            "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY"),
            "DRIVER_CHAT_LLM_MODEL": os.getenv("DRIVER_CHAT_LLM_MODEL", "gemini-2.5-flash"),
            "REDIS_URL": os.getenv("REDIS_URL"),
            "TWILIO_ACCOUNT_SID": os.getenv("TWILIO_ACCOUNT_SID"),
            "TWILIO_AUTH_TOKEN": os.getenv("TWILIO_AUTH_TOKEN"),
            "TWILIO_FROM_NUMBER": os.getenv("TWILIO_FROM_NUMBER"),
        }
        return Settings.model_validate(data)  # type: ignore[attr-defined]
    return Settings(
        SUPABASE_URL=os.getenv("SUPABASE_URL"),
        SUPABASE_PUBLISHABLE_KEY=os.getenv("SUPABASE_PUBLISHABLE_KEY"),
        DATA_BACKEND=os.getenv("DATA_BACKEND", "local"),
        ENVIRONMENT=os.getenv("ENVIRONMENT", "development"),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY"),
        DRIVER_CHAT_LLM_MODEL=os.getenv("DRIVER_CHAT_LLM_MODEL", "gemini-2.5-flash"),
        REDIS_URL=os.getenv("REDIS_URL"),
        TWILIO_ACCOUNT_SID=os.getenv("TWILIO_ACCOUNT_SID"),
        TWILIO_AUTH_TOKEN=os.getenv("TWILIO_AUTH_TOKEN"),
        TWILIO_FROM_NUMBER=os.getenv("TWILIO_FROM_NUMBER"),
    )
