"""Application configuration, loaded once from environment / .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root when running from source (src/orchestrator/config.py -> 3 levels up).
# In a container the .env file won't exist there; real env vars take over.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://orchestrator:orchestrator@localhost:5432/orchestrator"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None

    # Role → model routes, as "provider:model". Strong models where judgment
    # matters, cheap models for high-volume specialist work.
    supervisor_model: str = "anthropic:claude-sonnet-5"
    specialist_model: str = "anthropic:claude-haiku-4-5"
    reviewer_model: str = "openai:gpt-5"
    # Dev flag: force the cheapest model on every provider regardless of routes.
    llm_cheap_mode: bool = False

    # Shared scratch space for agent file tools (created on demand, gitignored).
    workspace_dir: str = str(_REPO_ROOT / "sandbox-workspace")

    # Escalation policy (HITL) — thresholds are ops-tunable, not code.
    escalation_confidence_floor: float = 0.35
    escalation_review_floor: float = 0.40
    require_plan_approval: bool = False


@lru_cache
def get_settings() -> Settings:
    """Cached accessor — one Settings instance per process, importable anywhere."""
    return Settings()


def redis_url_with_db(url: str, db: int) -> str:
    """Pin a Redis URL to a logical DB, whatever db the base URL names.

    Documented DB map: /0 celery broker · /1 celery results · /2 working
    memory · /3 rate limits.
    """
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{db}"))
