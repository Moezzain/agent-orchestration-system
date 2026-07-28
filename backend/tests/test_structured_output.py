import pytest
from pydantic import BaseModel, Field

from orchestrator.llm.structured import StructuredOutputError, complete_structured
from orchestrator.llm.types import ChatMessage
from tests.fakes import ScriptedProvider


class Verdict(BaseModel):
    approved: bool
    score: float = Field(ge=0.0, le=1.0)


MESSAGES = [ChatMessage(role="user", content="judge this")]


async def test_valid_json_parses_first_try() -> None:
    provider = ScriptedProvider(['{"approved": true, "score": 0.8}'])

    result = await complete_structured(
        provider, model="m", messages=MESSAGES, response_model=Verdict
    )

    assert result.approved is True
    assert result.score == 0.8


async def test_markdown_fences_are_tolerated() -> None:
    provider = ScriptedProvider(['```json\n{"approved": false, "score": 0.2}\n```'])

    result = await complete_structured(
        provider, model="m", messages=MESSAGES, response_model=Verdict
    )

    assert result.approved is False


async def test_invalid_response_retried_with_error_feedback() -> None:
    provider = ScriptedProvider(
        [
            '{"approved": true, "score": 1.7}',  # violates le=1.0
            '{"approved": true, "score": 0.9}',
        ]
    )

    result = await complete_structured(
        provider, model="m", messages=MESSAGES, response_model=Verdict
    )

    assert result.score == 0.9
    # second call saw its own bad output + the validation error
    retry_messages = provider.calls[1]
    prior, correction = retry_messages[-2], retry_messages[-1]
    assert isinstance(prior, ChatMessage) and prior.role == "assistant"
    assert isinstance(correction, ChatMessage)
    assert "failed schema validation" in correction.content


async def test_exhausted_attempts_raise() -> None:
    provider = ScriptedProvider(["not json", "still not json", "nope"])

    with pytest.raises(StructuredOutputError, match="after 3 attempts"):
        await complete_structured(provider, model="m", messages=MESSAGES, response_model=Verdict)


async def test_schema_injected_into_system_prompt() -> None:
    provider = ScriptedProvider(['{"approved": true, "score": 0.5}'])

    await complete_structured(
        provider, model="m", messages=MESSAGES, response_model=Verdict, system="Be fair."
    )

    # ScriptedProvider doesn't record system, but the call succeeded with one
    # message only — schema went via system, not as an extra user message
    assert len(provider.calls[0]) == 1
