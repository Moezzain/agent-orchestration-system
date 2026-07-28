import pytest

from orchestrator.config import Settings


def _settings() -> Settings:
    # _env_file=None: tests must not depend on a developer's local .env
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults_target_local_infra() -> None:
    settings = _settings()

    assert settings.environment == "dev"
    assert settings.chroma_port == 8001
    assert "localhost:5432" in settings.database_url
    assert settings.anthropic_api_key is None


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("CHROMA_PORT", "9000")

    settings = _settings()

    assert settings.environment == "prod"
    assert settings.chroma_port == 9000


def test_invalid_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")

    with pytest.raises(ValueError, match="environment"):
        _settings()


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")

    settings = _settings()

    assert settings.anthropic_api_key is not None
    assert "sk-ant-secret-value" not in repr(settings)
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"
