from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from typing import Any

from products.ai.engine.memory_extractor import MemoryExtractor
from products.ai.engine.providers import OllamaProvider
from products.ai.engine.response_engine import ResponseDecisionEngine
from products.ai.engine.test_report import AITestReportStore
from products.ai.engine.training_store import TrainingStore
from shared.streamhouse_shared.protocol import (
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    buffered_message_from_dict,
    extracted_memory_to_dict,
    response_decision_to_dict,
    response_message_from_dict,
    response_decision_from_dict,
)
from products.ai.streamhouse_ai.settings import StreamhouseAISettings, StreamhouseAISettingsStore


class StreamhouseAIService:
    def __init__(
        self,
        training_store: TrainingStore | None = None,
        test_report_store: AITestReportStore | None = None,
        ai_settings: StreamhouseAISettings | None = None,
        settings_store: StreamhouseAISettingsStore | None = None,
    ) -> None:
        self.decision_history: list[dict[str, Any]] = []
        self.memory_history: list[dict[str, Any]] = []
        self.last_hub_contact = 0.0
        self.training_store = training_store or TrainingStore()
        self.test_report_store = test_report_store or AITestReportStore()
        self.settings_store = settings_store or StreamhouseAISettingsStore()
        if ai_settings is not None:
            self.settings = ai_settings
        else:
            try:
                self.settings = self.settings_store.load()
            except (OSError, ValueError):
                self.settings = StreamhouseAISettings()
        for store in (self.training_store, self.test_report_store):
            try:
                store.load()
            except (OSError, ValueError):
                pass

    def status(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = self._provider(timeout=5.0)
        status = provider.status()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "available": status.available,
            "models": list(status.models),
            "error": status.error,
            "selected_model": provider.model,
            "ollama_endpoint": self.settings.ollama_endpoint,
        }

    def ping(self, _body: dict[str, Any]) -> dict[str, Any]:
        self.last_hub_contact = monotonic()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "available": True,
            "models": [],
            "error": "",
        }

    def decisions(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = tuple(
            response_message_from_dict(item)
            for item in body.get("messages", [])
            if isinstance(item, dict)
        )
        recent_chat = tuple(
            {str(key): str(value) for key, value in item.items()}
            for item in body.get("recent_chat", [])
            if isinstance(item, dict)
        )
        decisions = ResponseDecisionEngine().decide(
            self._provider(timeout=60.0),
            messages,
            recent_chat,
            self.settings.personality,
            self.settings.allow_mild_profanity,
            self.settings.allow_strong_profanity,
        )
        self.decision_history.extend(
            response_decision_to_dict(item) for item in decisions
        )
        self.decision_history = self.decision_history[-500:]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decisions": [response_decision_to_dict(item) for item in decisions],
        }

    def memories(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = tuple(
            buffered_message_from_dict(item)
            for item in body.get("messages", [])
            if isinstance(item, dict)
        )
        memories = MemoryExtractor().extract(
            self._provider(timeout=120.0),
            str(body.get("user_name", "")),
            messages,
            tuple(str(item) for item in body.get("existing_memories", [])),
        )
        self.memory_history.extend(
            {
                "user_name": str(body.get("user_name", "")),
                **extracted_memory_to_dict(item),
            }
            for item in memories
        )
        self.memory_history = self.memory_history[-500:]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "memories": [extracted_memory_to_dict(item) for item in memories],
        }

    def ai_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action", "get"))
        if action == "set":
            values = body.get("settings", {})
            if not isinstance(values, dict):
                raise ValueError("Streamhouse AI settings must be an object.")
            self.settings = StreamhouseAISettings.from_dict(values)
            self.settings_store.save(self.settings)
        elif action != "get":
            raise ValueError("Unknown Streamhouse AI settings action.")
        return {"settings": self.settings.to_dict()}

    def training(self, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action", "load"))
        result: dict[str, Any] = {}
        if action == "load":
            self.training_store.load()
        elif action == "capture":
            decision = body.get("decision", {})
            if not isinstance(decision, dict):
                raise ValueError("Training decision must be an object.")
            result["example_id"] = self.training_store.capture(
                str(body.get("user_id", "")), response_decision_from_dict(decision)
            )
        elif action == "label":
            result["changed"] = self.training_store.label(
                str(body.get("example_id", "")), str(body.get("label", ""))
            )
        elif action == "delete":
            result["changed"] = self.training_store.delete(
                str(body.get("example_id", ""))
            )
        elif action == "delete_participant":
            result["removed"] = self.training_store.delete_participant(
                str(body.get("user_id", ""))
            )
        elif action == "clear":
            result["removed"] = self.training_store.clear()
        else:
            raise ValueError("Unknown training action.")
        result["examples"] = self.training_store.examples
        return result

    def test_report(self, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action", "load"))
        result: dict[str, Any] = {}
        if action == "load":
            self.test_report_store.load()
        elif action == "save":
            self.test_report_store.save()
        elif action == "record":
            self.test_report_store.record(
                outcome=str(body.get("outcome", "unknown")),
                reason=str(body.get("reason", "unknown")),
                latency_ms=int(body.get("latency_ms", 0)),
                response_expected=bool(body.get("response_expected", False)),
                confidence=float(body.get("confidence", 0.0)),
                save=bool(body.get("save", True)),
            )
        elif action == "clear":
            result["removed"] = self.test_report_store.clear()
        elif action == "start_new_session":
            self.test_report_store.start_new_session()
        elif action == "selected":
            result["selected"] = self.test_report_store.selected_events(
                bool(body.get("current_session_only", True))
            )
        elif action == "summary":
            result["summary"] = self.test_report_store.summary(
                bool(body.get("current_session_only", True))
            )
        else:
            raise ValueError("Unknown test-report action.")
        result["events"] = self.test_report_store.events
        return result

    def _provider(self, timeout: float) -> OllamaProvider:
        return OllamaProvider(
            self.settings.ollama_endpoint,
            self.settings.model,
            timeout=timeout,
        )


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    service: StreamhouseAIService | None = None,
) -> ThreadingHTTPServer:
    reasoning = service or StreamhouseAIService()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            requested_version = self._requested_protocol_version()
            if requested_version is None:
                self._send(
                    409,
                    {
                        "error": (
                            "Unsupported Streamhouse protocol version; "
                            f"expected {PROTOCOL_VERSION}."
                        )
                    },
                )
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("Request body must be an object.")
                routes = {
                    "/v1/status": reasoning.status,
                    "/v1/decisions": reasoning.decisions,
                    "/v1/memories": reasoning.memories,
                }
                if hasattr(reasoning, "ping"):
                    routes["/v1/ping"] = reasoning.ping
                if hasattr(reasoning, "training"):
                    routes["/v1/training"] = reasoning.training
                if hasattr(reasoning, "test_report"):
                    routes["/v1/test-report"] = reasoning.test_report
                if hasattr(reasoning, "ai_settings"):
                    routes["/v1/settings"] = reasoning.ai_settings
                operation = routes.get(self.path)
                if operation is None:
                    self._send(404, {"error": "Unknown endpoint."})
                    return
                self._send(200, operation(body))
            except Exception as error:
                self._send(500, {"error": str(error)})

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _requested_protocol_version(self) -> int | None:
            current = self.headers.get(PROTOCOL_HEADER)
            if current == str(PROTOCOL_VERSION):
                return PROTOCOL_VERSION
            return None

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
