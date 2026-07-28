"""WebSearchTool tests using httpx.MockTransport — no network."""

import json

import httpx
import pytest

from orchestrator.tools.builtin.web_search import WebSearchArgs, WebSearchTool


def _transport_returning(
    payload: dict[str, object],
    status: int = 200,
    sent_bodies: list[dict[str, object]] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if sent_bodies is not None:
            sent_bodies.append(json.loads(request.content))
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


async def test_results_formatted_for_llm_consumption() -> None:
    transport = _transport_returning(
        {
            "results": [
                {"title": "Chroma docs", "url": "https://a.io", "content": "Vector DB..."},
                {"title": "pgvector", "url": "https://b.io", "content": "Postgres ext..."},
            ]
        }
    )
    tool = WebSearchTool("tvly-key", transport=transport)

    output = await tool.execute(WebSearchArgs(query="chroma vs pgvector"))

    assert "[1] Chroma docs" in output
    assert "https://b.io" in output


async def test_request_carries_query_key_and_limit() -> None:
    sent_bodies: list[dict[str, object]] = []
    transport = _transport_returning({"results": []}, sent_bodies=sent_bodies)
    tool = WebSearchTool("tvly-key", transport=transport)

    await tool.execute(WebSearchArgs(query="test query", max_results=3))

    assert sent_bodies == [{"api_key": "tvly-key", "query": "test query", "max_results": 3}]


async def test_empty_results_say_so() -> None:
    tool = WebSearchTool("k", transport=_transport_returning({"results": []}))

    output = await tool.execute(WebSearchArgs(query="gibberish xyzzy"))

    assert "no results" in output


async def test_http_error_propagates_for_registry_to_catch() -> None:
    tool = WebSearchTool("bad-key", transport=_transport_returning({}, status=401))

    with pytest.raises(httpx.HTTPStatusError):
        await tool.execute(WebSearchArgs(query="anything"))
