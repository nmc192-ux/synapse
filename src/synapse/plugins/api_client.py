import asyncio
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from synapse.runtime.tools import ToolRegistry


def register(registry: ToolRegistry) -> None:
    async def api_request(arguments: dict[str, object]) -> dict[str, object]:
        method = str(arguments.get("method", "GET")).upper()
        url = str(arguments.get("url", "")).strip()
        if not url:
            raise ValueError("api.request requires a 'url'.")

        params = arguments.get("params")
        if isinstance(params, dict) and params:
            query = urlencode({key: str(value) for key, value in params.items()})
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"

        headers = {
            str(key): str(value)
            for key, value in (arguments.get("headers", {}) or {}).items()
        }
        body = arguments.get("json")
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        if payload is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        request = Request(url, data=payload, headers=headers, method=method)
        response = await asyncio.to_thread(_fetch_response, request)
        return response

    registry.register(
        "api.request",
        api_request,
        description="Call arbitrary HTTP API endpoints and return structured JSON/text.",
        plugin_name="api_client",
    )


def _fetch_response(request: Request) -> dict[str, object]:
    with urlopen(request, timeout=20) as response:
        content_type = response.headers.get("Content-Type", "")
        raw = response.read().decode("utf-8")
        parsed_body: object
        if "application/json" in content_type:
            parsed_body = json.loads(raw)
        else:
            parsed_body = raw
        return {
            "status": response.status,
            "content_type": content_type,
            "body": parsed_body,
        }
