from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class LocalAIStatus:
    available: bool
    models: tuple[str, ...] = ()
    error: str = ""


class OllamaProvider:
    """Small provider-neutral boundary around Ollama's local HTTP API."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:11434",
        model: str = "qwen3:14b",
        timeout: float = 120.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout

    def status(self) -> LocalAIStatus:
        try:
            payload = self._request("GET", "/api/tags")
            models = tuple(
                str(item.get("name", ""))
                for item in payload.get("models", [])
                if isinstance(item, dict) and item.get("name")
            )
            return LocalAIStatus(True, models)
        except Exception as error:
            return LocalAIStatus(False, error=str(error))

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        think: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": think,
        }
        if tools:
            body["tools"] = tools
        return self._request("POST", "/api/chat", body)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            self.endpoint + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Local AI returned an invalid response.")
        return payload
