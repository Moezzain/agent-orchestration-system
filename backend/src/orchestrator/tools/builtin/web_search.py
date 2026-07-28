"""Web search via the Tavily API.

Tavily over scraping Google/Bing: purpose-built for LLM consumption (clean
text snippets, no HTML), single POST, generous free tier.
"""

import httpx
from pydantic import BaseModel, Field

from orchestrator.tools.base import Tool

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT_SECONDS = 15.0


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=3, description="What to search the web for.")
    max_results: int = Field(default=5, ge=1, le=10, description="Number of results.")


class WebSearchTool(Tool[WebSearchArgs]):
    name = "web_search"
    description = "Search the web; returns titles, URLs, and content snippets."
    args_model = WebSearchArgs

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._transport = transport  # injectable for tests

    async def execute(self, args: WebSearchArgs) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, transport=self._transport) as client:
            response = await client.post(
                _TAVILY_URL,
                json={
                    "api_key": self._api_key,
                    "query": args.query,
                    "max_results": args.max_results,
                },
            )
            response.raise_for_status()
            payload = response.json()

        results = payload.get("results", [])
        if not results:
            return f"no results for {args.query!r}"

        blocks = [
            f"[{i}] {r.get('title', 'untitled')}\n{r.get('url', '')}\n{r.get('content', '')}"
            for i, r in enumerate(results, start=1)
        ]
        return "\n\n".join(blocks)
