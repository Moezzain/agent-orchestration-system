"""Role → (provider, model) routing.

The multi-model story lives here: strong models where judgment matters
(supervisor plans, reviewer judges), cheap models for high-volume specialist
work. Routes are config, not code — swap models without touching call sites.
"""

from collections.abc import Mapping

from orchestrator.config import Settings
from orchestrator.llm.provider import LLMProvider
from orchestrator.schemas import AgentRole

# Cheapest usable model per provider, used when llm_cheap_mode is on (dev flag:
# iterate on prompts/graph wiring without paying strong-model prices).
_CHEAP_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
}


class ModelRoute:
    def __init__(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model


class ModelRouter:
    def __init__(self, settings: Settings, providers: Mapping[str, LLMProvider]) -> None:
        self._providers = providers
        self._cheap_mode = settings.llm_cheap_mode
        self._route_specs: dict[AgentRole, str] = {
            AgentRole.SUPERVISOR: settings.supervisor_model,
            AgentRole.SPECIALIST: settings.specialist_model,
            AgentRole.REVIEWER: settings.reviewer_model,
        }

    def resolve(self, role: AgentRole) -> ModelRoute:
        spec = self._route_specs[role]
        provider_name, _, model = spec.partition(":")
        if not model:
            raise ValueError(f"invalid model spec {spec!r} — expected 'provider:model'")
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ValueError(
                f"role {role.value!r} routes to provider {provider_name!r}, "
                f"but no API key is configured for it"
            )
        if self._cheap_mode:
            model = _CHEAP_MODELS.get(provider_name, model)
        return ModelRoute(provider, model)


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Construct one adapter per configured API key. Missing key → provider absent."""
    from orchestrator.llm.anthropic_provider import AnthropicProvider
    from orchestrator.llm.openai_provider import OpenAIProvider

    providers: dict[str, LLMProvider] = {}
    if settings.anthropic_api_key is not None:
        providers["anthropic"] = AnthropicProvider(settings.anthropic_api_key.get_secret_value())
    if settings.openai_api_key is not None:
        providers["openai"] = OpenAIProvider(settings.openai_api_key.get_secret_value())
    return providers
