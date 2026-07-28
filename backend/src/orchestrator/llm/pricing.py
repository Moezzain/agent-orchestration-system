"""Static pricing table and per-call cost computation.

Prices are USD per million tokens (input, output), copied from provider
pricing pages. They drift over time — treat this file as config that needs an
occasional refresh, and the computed cost as an estimate for observability,
not billing-grade accounting.
"""

from orchestrator.llm.types import Usage
from orchestrator.logging import get_logger

logger = get_logger(__name__)

_MILLION = 1_000_000

# model → (input USD per 1M tokens, output USD per 1M tokens)
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # OpenAI
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
}


def _lookup(model: str) -> tuple[float, float] | None:
    """Providers return dated ids (claude-haiku-4-5-20251001) where we price
    aliases (claude-haiku-4-5) — longest-prefix match bridges the two."""
    exact = PRICES_PER_MTOK.get(model)
    if exact is not None:
        return exact
    candidates = [name for name in PRICES_PER_MTOK if model.startswith(name)]
    if not candidates:
        return None
    return PRICES_PER_MTOK[max(candidates, key=len)]


def compute_cost_usd(model: str, usage: Usage) -> float:
    """Cost of one call. Unknown models cost 0 and log a warning — observability
    code must never break execution."""
    prices = _lookup(model)
    if prices is None:
        logger.warning("unknown_model_pricing", model=model)
        return 0.0
    input_price, output_price = prices
    return (usage.input_tokens / _MILLION) * input_price + (
        usage.output_tokens / _MILLION
    ) * output_price
