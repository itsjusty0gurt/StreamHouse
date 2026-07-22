from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

from sally_shared.models import (
    BufferedChatMessage,
    ExtractedMemory,
    ResponseDecision,
    ResponseMessage,
)
from sally_companion.protocol import (
    PROTOCOL_VERSION,
    buffered_message_to_dict,
    extracted_memory_from_dict,
    response_decision_from_dict,
    response_message_to_dict,
)


@dataclass(frozen=True, slots=True)
class CompanionStatus:
    available: bool
    protocol_version: int = 0
    models: tuple[str, ...] = ()
    error: str = ""


class CompanionClient:
    """Synchronous localhost client used inside Qt worker threads."""

    def __init__(self, endpoint: str, timeout: float = 120.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def status(self, ollama_endpoint: str, model: str) -> CompanionStatus:
        try:
            payload = self._request(
                "/v1/status",
                {"ollama_endpoint": ollama_endpoint, "model": model},
            )
            version = int(payload.get("protocol_version", 0))
            if version != PROTOCOL_VERSION:
                return CompanionStatus(
                    False,
                    version,
                    error=(
                        f"Protocol mismatch: bot uses {PROTOCOL_VERSION}, "
                        f"companion uses {version}."
                    ),
                )
            return CompanionStatus(
                bool(payload.get("available", False)),
                version,
                tuple(str(item) for item in payload.get("models", [])),
                str(payload.get("error", "")),
            )
        except Exception as error:
            return CompanionStatus(False, error=str(error))

    def ping(self) -> CompanionStatus:
        try:
            payload = self._request("/v1/ping", {})
            version = int(payload.get("protocol_version", 0))
            return CompanionStatus(
                bool(payload.get("available", False))
                and version == PROTOCOL_VERSION,
                version,
                error=(
                    ""
                    if version == PROTOCOL_VERSION
                    else f"Protocol mismatch: {version}."
                ),
            )
        except Exception as error:
            return CompanionStatus(False, error=str(error))

    def decide(
        self,
        messages: tuple[ResponseMessage, ...],
        recent_chat: tuple[dict[str, str], ...],
        ollama_endpoint: str,
        model: str,
        personality: str,
        allow_mild_profanity: bool,
        allow_strong_profanity: bool,
    ) -> tuple[ResponseDecision, ...]:
        payload = self._request(
            "/v1/decisions",
            {
                "ollama_endpoint": ollama_endpoint,
                "model": model,
                "messages": [response_message_to_dict(item) for item in messages],
                "recent_chat": list(recent_chat),
                "personality": personality,
                "allow_mild_profanity": allow_mild_profanity,
                "allow_strong_profanity": allow_strong_profanity,
            },
        )
        return tuple(
            response_decision_from_dict(item)
            for item in payload.get("decisions", [])
            if isinstance(item, dict)
        )

    def extract_memories(
        self,
        user_name: str,
        messages: tuple[BufferedChatMessage, ...],
        existing_memories: tuple[str, ...],
        ollama_endpoint: str,
        model: str,
    ) -> tuple[ExtractedMemory, ...]:
        payload = self._request(
            "/v1/memories",
            {
                "ollama_endpoint": ollama_endpoint,
                "model": model,
                "user_name": user_name,
                "messages": [buffered_message_to_dict(item) for item in messages],
                "existing_memories": list(existing_memories),
            },
        )
        return tuple(
            extracted_memory_from_dict(item)
            for item in payload.get("memories", [])
            if isinstance(item, dict)
        )

    def _request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.endpoint + path,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Sally-Protocol": str(PROTOCOL_VERSION),
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("AI Companion returned an invalid response.")
        return payload

    def request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Call a versioned Companion control endpoint."""
        return self._request(path, body)

    def get_settings(self) -> dict[str, Any]:
        return dict(self._request("/v1/settings", {"action": "get"}).get("settings", {}))

    def update_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        return dict(
            self._request(
                "/v1/settings", {"action": "set", "settings": settings}
            ).get("settings", {})
        )
