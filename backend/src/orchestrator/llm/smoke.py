"""One-shot CLI smoke test for the LLM layer.

Usage:
    uv run python -m orchestrator.llm.smoke "What is 2+2?"
Requires ANTHROPIC_API_KEY (or .env at repo root).
"""

import asyncio
import sys

from orchestrator.config import get_settings
from orchestrator.llm import AnthropicProvider, ChatMessage

SMOKE_MODEL = "claude-haiku-4-5"  # cheapest — this is a plumbing test, not a quality test


async def main() -> None:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        sys.exit("ANTHROPIC_API_KEY not set — add it to .env at repo root")

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Say OK."
    provider = AnthropicProvider(settings.anthropic_api_key.get_secret_value())

    response = await provider.complete(
        model=SMOKE_MODEL,
        messages=[ChatMessage(role="user", content=prompt)],
        max_tokens=256,
    )

    print(response.text)
    print(
        f"\n[{response.provider}/{response.model}] "
        f"in={response.usage.input_tokens} out={response.usage.output_tokens} "
        f"stop={response.stop_reason} latency={response.latency_ms}ms"
    )


if __name__ == "__main__":
    asyncio.run(main())
