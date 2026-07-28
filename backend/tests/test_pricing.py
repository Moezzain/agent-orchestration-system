from orchestrator.llm.pricing import compute_cost_usd
from orchestrator.llm.types import Usage


def test_known_model_cost_math() -> None:
    # claude-sonnet-5: $3/M input, $15/M output
    usage = Usage(input_tokens=1_000_000, output_tokens=200_000)

    cost = compute_cost_usd("claude-sonnet-5", usage)

    assert cost == 3.00 + 0.2 * 15.00  # 6.0


def test_small_call_costs_fractions_of_a_cent() -> None:
    # typical specialist call on haiku: $1/M in, $5/M out
    usage = Usage(input_tokens=2_000, output_tokens=500)

    cost = compute_cost_usd("claude-haiku-4-5", usage)

    assert round(cost, 6) == round(2_000 / 1e6 * 1.0 + 500 / 1e6 * 5.0, 6)  # $0.0045


def test_unknown_model_costs_zero_not_crash() -> None:
    usage = Usage(input_tokens=100, output_tokens=100)

    assert compute_cost_usd("some-future-model", usage) == 0.0


def test_dated_model_id_resolves_via_prefix() -> None:
    # Anthropic returns dated ids; pricing table holds aliases
    usage = Usage(input_tokens=1_000_000, output_tokens=0)

    assert compute_cost_usd("claude-haiku-4-5-20251001", usage) == 1.00
