from __future__ import annotations

from abc import ABC, abstractmethod
import json
from typing import Any

import httpx


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate text from the configured model provider."""


def estimate_token_count(content: str | dict[str, Any] | list[Any] | None) -> int:
    if content is None:
        return 0
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=True)
    return max(1, len(content) // 4) if content else 0


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def generate(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages, "temperature": 0},
            )
            response.raise_for_status()
            payload = response.json()

        choices = payload.get("choices", [])
        if not choices:
            raise RuntimeError("OpenAI provider returned no choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("OpenAI provider returned an invalid response payload.")
        return content


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout_seconds: float = 60.0,
        api_version: str = "2023-06-01",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.api_version = api_version

    async def generate(self, prompt: str, system: str | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.api_version,
                },
                json=body,
            )
            response.raise_for_status()
            payload = response.json()

        content = payload.get("content", [])
        if not isinstance(content, list):
            raise RuntimeError("Anthropic provider returned an invalid response payload.")

        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if not parts:
            raise RuntimeError("Anthropic provider returned no text content.")
        return "\n".join(parts)


class LocalModelProvider(LLMProvider):
    def __init__(
        self,
        endpoint: str,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def generate(self, prompt: str, system: str | None = None) -> str:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {"prompt": prompt}
        if system is not None:
            payload["system"] = system
        if self.model is not None:
            payload["model"] = self.model

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        if isinstance(data.get("response"), str):
            return data["response"]
        if isinstance(data.get("text"), str):
            return data["text"]

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
                if isinstance(first_choice.get("text"), str):
                    return first_choice["text"]

        raise RuntimeError("Local model provider returned an unsupported response payload.")


def create_llm_provider(settings: Any) -> LLMProvider | None:
    provider_name = getattr(settings, "llm_provider", None)
    if provider_name is None:
        return None

    normalized = str(provider_name).strip().lower()
    if normalized in {"", "none", "disabled"}:
        return None

    timeout_seconds = float(getattr(settings, "llm_request_timeout_seconds", 60.0))

    if normalized == "openai":
        api_key = getattr(settings, "openai_api_key", None)
        if not api_key:
            raise ValueError("SYNAPSE_LLM_PROVIDER is set to openai but OPENAI_API_KEY is missing.")
        return OpenAIProvider(
            api_key=api_key,
            model=getattr(settings, "openai_model", "gpt-4o-mini"),
            base_url=getattr(settings, "openai_base_url", "https://api.openai.com/v1"),
            timeout_seconds=timeout_seconds,
        )

    if normalized == "anthropic":
        api_key = getattr(settings, "anthropic_api_key", None)
        if not api_key:
            raise ValueError("SYNAPSE_LLM_PROVIDER is set to anthropic but ANTHROPIC_API_KEY is missing.")
        return AnthropicProvider(
            api_key=api_key,
            model=getattr(settings, "anthropic_model", "claude-3-5-sonnet-latest"),
            base_url=getattr(settings, "anthropic_base_url", "https://api.anthropic.com/v1"),
            timeout_seconds=timeout_seconds,
            api_version=getattr(settings, "anthropic_api_version", "2023-06-01"),
        )

    if normalized == "local":
        return LocalModelProvider(
            endpoint=getattr(settings, "local_model_endpoint", "http://127.0.0.1:11434/api/generate"),
            model=getattr(settings, "local_model_name", None),
            api_key=getattr(settings, "local_model_api_key", None),
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(f"Unsupported LLM provider: {provider_name}")
