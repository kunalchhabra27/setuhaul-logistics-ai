"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache
import os

from pydantic import BaseModel, Field, field_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal runtimes
    BaseSettings = BaseModel

    class SettingsConfigDict(dict):  # type: ignore[override]
        """Fallback config container when pydantic-settings is unavailable."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_publishable_key: str = Field(alias="SUPABASE_PUBLISHABLE_KEY")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("supabase_url")
    @classmethod
    def normalize_supabase_url(cls, value: str) -> str:
        """Remove trailing slashes so client paths remain stable."""
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    if hasattr(Settings, "model_validate"):
        data = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_PUBLISHABLE_KEY": os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            "ENVIRONMENT": os.getenv("ENVIRONMENT", "development"),
            "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        }
        return Settings.model_validate(data)  # type: ignore[attr-defined]
    return Settings(
        SUPABASE_URL=os.getenv("SUPABASE_URL"),
        SUPABASE_PUBLISHABLE_KEY=os.getenv("SUPABASE_PUBLISHABLE_KEY"),
        ENVIRONMENT=os.getenv("ENVIRONMENT", "development"),
        LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
    )
