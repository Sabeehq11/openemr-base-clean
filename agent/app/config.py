"""Typed settings. Secrets come only from the environment / git-ignored agent/.env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openemr_base_url: str = "https://localhost:9300"
    openemr_verify_tls: bool = True
    llm_provider: str = "stub"  # stub | openrouter
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    module_signing_secret: SecretStr = SecretStr("")
    audit_hmac_key: SecretStr = SecretStr("")
    dashboard_token: SecretStr = SecretStr("")  # empty = dashboard disabled (404)
    oauth_client_id: str = ""
    oauth_client_secret: SecretStr = SecretStr("")
    data_dir: str = "./data"
    tool_timeout_s: float = 5.0
    max_inflight_chats: int = 0  # opt-in cap on chats in progress; over it, 503 + Retry-After (docs/PERFORMANCE.md: not a net win, so off)
    rate_limit_per_min: int = 60  # per verified user across chat and tool calls; 0 disables


@lru_cache
def get_settings() -> Settings:
    return Settings()
