"""Structured output: force an LLM response into a Pydantic model.

Provider-agnostic strategy — prompt for JSON matching the model's schema,
parse, and on failure feed the validation error back for a corrected attempt.
The retry-with-error loop is what makes this reliable: models fix their own
JSON when shown exactly what was wrong.
"""

import json

from pydantic import BaseModel, ValidationError

from orchestrator.llm.provider import LLMProvider
from orchestrator.llm.types import ChatMessage


class StructuredOutputError(Exception):
    """All attempts exhausted without a schema-valid response."""


def _schema_instructions(response_model: type[BaseModel]) -> str:
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    return (
        "\n\nRespond with a single JSON object matching this JSON schema exactly. "
        "No prose, no markdown fences, JSON only.\n"
        f"Schema:\n{schema}"
    )


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.index("\n") if "\n" in stripped else len(stripped)
        stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


async def complete_structured[T: BaseModel](
    provider: LLMProvider,
    *,
    model: str,
    messages: list[ChatMessage],
    response_model: type[T],
    system: str | None = None,
    max_tokens: int = 4096,
    max_attempts: int = 3,
) -> T:
    full_system = (system or "") + _schema_instructions(response_model)
    conversation = list(messages)

    last_error = ""
    for _ in range(max_attempts):
        response = await provider.complete(
            model=model,
            messages=conversation,
            system=full_system,
            max_tokens=max_tokens,
        )
        try:
            return response_model.model_validate_json(_strip_fences(response.text))
        except ValidationError as exc:
            last_error = str(exc)
            # show the model its own output and the exact failure; it corrects
            conversation = [
                *conversation,
                ChatMessage(role="assistant", content=response.text),
                ChatMessage(
                    role="user",
                    content=(
                        f"That response failed schema validation:\n{last_error}\n"
                        "Reply again with corrected JSON only."
                    ),
                ),
            ]

    raise StructuredOutputError(
        f"no schema-valid response after {max_attempts} attempts; last error: {last_error}"
    )
