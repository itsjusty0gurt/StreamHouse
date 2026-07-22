from __future__ import annotations

from sally_companion.client import CompanionClient
from sally_companion.protocol import response_decision_to_dict
from sally_shared.models import ResponseDecision


class CompanionTrainingStore:
    """Remote-control facade for training data owned by AI Companion."""

    LABELS = (
        "direct",
        "conversation",
        "interjection",
        "ignore",
        "conversation_end",
    )

    def __init__(self, endpoint: str = "http://127.0.0.1:8765") -> None:
        self.client = CompanionClient(endpoint, timeout=10.0)
        self.examples: list[dict[str, object]] = []

    def configure(self, endpoint: str) -> None:
        self.client = CompanionClient(endpoint, timeout=10.0)

    def _call(self, action: str, **values: object) -> dict:
        result = self.client.request("/v1/training", {"action": action, **values})
        examples = result.get("examples")
        if isinstance(examples, list):
            self.examples = [item for item in examples if isinstance(item, dict)]
        return result

    def load(self) -> None:
        return

    def connect(self) -> None:
        self._call("load")

    def capture(self, user_id: str, decision: ResponseDecision) -> str:
        result = self._call(
            "capture",
            user_id=user_id,
            decision=response_decision_to_dict(decision),
        )
        return str(result.get("example_id", ""))

    def label(self, example_id: str, label: str) -> bool:
        return bool(self._call("label", example_id=example_id, label=label).get("changed"))

    def delete(self, example_id: str) -> bool:
        return bool(self._call("delete", example_id=example_id).get("changed"))

    def delete_participant(self, user_id: str) -> int:
        return int(self._call("delete_participant", user_id=user_id).get("removed", 0))

    def clear(self) -> int:
        return int(self._call("clear").get("removed", 0))


class CompanionTestReportStore:
    """Remote-control facade for AI diagnostics owned by AI Companion."""

    def __init__(self, endpoint: str = "http://127.0.0.1:8765") -> None:
        self.client = CompanionClient(endpoint, timeout=10.0)
        self.events: list[dict[str, object]] = []
        self.connected = False

    def configure(self, endpoint: str) -> None:
        self.client = CompanionClient(endpoint, timeout=10.0)
        self.connected = False

    def _call(self, action: str, **values: object) -> dict:
        result = self.client.request("/v1/test-report", {"action": action, **values})
        self.connected = True
        events = result.get("events")
        if isinstance(events, list):
            self.events = [item for item in events if isinstance(item, dict)]
        return result

    def load(self) -> None:
        return

    def connect(self) -> None:
        self._call("load")

    def save(self) -> None:
        if self.connected:
            self._call("save")

    def record(self, **values: object) -> None:
        self._call("record", **values)

    def clear(self) -> int:
        return int(self._call("clear").get("removed", 0))

    def start_new_session(self) -> None:
        if not self.connected:
            return
        try:
            self._call("start_new_session")
        except OSError:
            return

    def selected_events(self, current_session_only: bool) -> list[dict[str, object]]:
        if not self.connected:
            return list(self.events)
        try:
            result = self._call("selected", current_session_only=current_session_only)
            return [
                item for item in result.get("selected", []) if isinstance(item, dict)
            ]
        except OSError:
            return list(self.events)

    def summary(self, current_session_only: bool = True) -> dict[str, object]:
        if not self.connected:
            return {
                "total": 0,
                "sent": 0,
                "ignored": 0,
                "missed": 0,
                "blocked": 0,
                "failed": 0,
                "average_latency_ms": 0,
            }
        try:
            return dict(
                self._call("summary", current_session_only=current_session_only).get(
                    "summary", {}
                )
            )
        except OSError:
            return {
                "total": 0,
                "sent": 0,
                "ignored": 0,
                "missed": 0,
                "blocked": 0,
                "failed": 0,
                "average_latency_ms": 0,
            }
