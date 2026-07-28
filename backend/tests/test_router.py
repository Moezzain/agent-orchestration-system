from collections.abc import Sequence

import pytest

from orchestrator.config import Settings
from orchestrator.llm import ConversationMessage, LLMResponse, ModelRouter, ToolSpec
from orchestrator.schemas import AgentRole


class FakeProvider:
    """Satisfies LLMProvider structurally — no inheritance, just shape.

    Parameter types must match the Protocol exactly (contravariance): declaring
    `list` here where the Protocol says `Sequence` would NOT satisfy it.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ConversationMessage],
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        raise NotImplementedError


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type, call-arg]


PROVIDERS = {"anthropic": FakeProvider("anthropic"), "openai": FakeProvider("openai")}


def test_roles_resolve_to_configured_routes() -> None:
    router = ModelRouter(_settings(), PROVIDERS)

    supervisor = router.resolve(AgentRole.SUPERVISOR)
    specialist = router.resolve(AgentRole.SPECIALIST)
    reviewer = router.resolve(AgentRole.REVIEWER)

    assert supervisor.provider.name == "anthropic"
    assert supervisor.model == "claude-sonnet-5"
    assert specialist.model == "claude-haiku-4-5"
    assert reviewer.provider.name == "openai"
    assert reviewer.model == "gpt-5"


def test_cheap_mode_downgrades_every_route() -> None:
    router = ModelRouter(_settings(llm_cheap_mode=True), PROVIDERS)

    assert router.resolve(AgentRole.SUPERVISOR).model == "claude-haiku-4-5"
    assert router.resolve(AgentRole.REVIEWER).model == "gpt-5-mini"


def test_unconfigured_provider_gives_actionable_error() -> None:
    router = ModelRouter(_settings(), {"anthropic": FakeProvider("anthropic")})

    with pytest.raises(ValueError, match="no API key is configured"):
        router.resolve(AgentRole.REVIEWER)  # routes to openai, which is absent


def test_invalid_route_spec_rejected() -> None:
    router = ModelRouter(_settings(supervisor_model="just-a-model-name"), PROVIDERS)

    with pytest.raises(ValueError, match="expected 'provider:model'"):
        router.resolve(AgentRole.SUPERVISOR)
