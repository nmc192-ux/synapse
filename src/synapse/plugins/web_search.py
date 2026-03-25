import asyncio
import json
from urllib.parse import urlencode
from urllib.request import urlopen

from synapse.runtime.tools import ToolRegistry


def register(registry: ToolRegistry) -> None:
    registry.register_plugin(
        name="web_search",
        module=__name__,
        capabilities=["web_search", "web_summary"],
        endpoint="web.search",
    )

    async def web_search(arguments: dict[str, object]) -> dict[str, object]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("web.search requires a non-empty 'query'.")

        params = urlencode(
            {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }
        )
        payload = await asyncio.to_thread(
            _fetch_json, f"https://api.duckduckgo.com/?{params}"
        )
        related_topics = payload.get("RelatedTopics", [])
        return {
            "query": query,
            "heading": payload.get("Heading", ""),
            "abstract": payload.get("AbstractText", ""),
            "results": _flatten_related_topics(related_topics),
        }

    registry.register(
        "web.search",
        web_search,
        description="Search the web and return structured summary results.",
        plugin_name="web_search",
    )


def _fetch_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _flatten_related_topics(related_topics: list[object]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for topic in related_topics[:10]:
        if isinstance(topic, dict) and "Topics" in topic:
            results.extend(_flatten_related_topics(topic["Topics"]))
            continue
        if isinstance(topic, dict):
            results.append(
                {
                    "text": topic.get("Text"),
                    "url": topic.get("FirstURL"),
                }
            )
    return results[:10]
