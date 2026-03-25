import asyncio
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from synapse.runtime.tools import ToolRegistry


def register(registry: ToolRegistry) -> None:
    registry.register_plugin(
        name="github_search",
        module=__name__,
        capabilities=["repository_search", "code_discovery"],
        endpoint="github.search",
    )

    async def github_search(arguments: dict[str, object]) -> dict[str, object]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("github.search requires a non-empty 'query'.")

        per_page = int(arguments.get("per_page", 5))
        params = urlencode({"q": query, "per_page": per_page})
        request = Request(
            f"https://api.github.com/search/repositories?{params}",
            headers=_headers(arguments),
        )
        payload = await asyncio.to_thread(_fetch_json, request)
        items = payload.get("items", [])
        return {
            "query": query,
            "total_count": payload.get("total_count", 0),
            "items": [
                {
                    "full_name": item.get("full_name"),
                    "description": item.get("description"),
                    "html_url": item.get("html_url"),
                    "stargazers_count": item.get("stargazers_count"),
                }
                for item in items
            ],
        }

    registry.register(
        "github.search",
        github_search,
        description="Search GitHub repositories and return structured results.",
        plugin_name="github_search",
    )


def _headers(arguments: dict[str, object]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "synapse-plugin",
    }
    token = arguments.get("token") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_json(request: Request) -> dict[str, object]:
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))
