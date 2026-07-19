from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root


class AITestReportStore:
    """Persist anonymous AI response diagnostics without retaining chat text."""

    MAX_EVENTS = 2_000

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "diagnostics" / "ai_test_report.json"
        self.session_id = uuid4().hex
        self.events: list[dict[str, object]] = []

    def load(self) -> None:
        if not self.path.exists():
            return
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("AI test report must contain a JSON object.")
        values = payload.get("events", [])
        self.events = [
            value
            for value in values
            if isinstance(value, dict)
            and isinstance(value.get("recorded_at"), str)
            and isinstance(value.get("outcome"), str)
        ][-self.MAX_EVENTS :]

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"version": 1, "events": self.events[-self.MAX_EVENTS :]},
        )

    def record(
        self,
        *,
        outcome: str,
        reason: str,
        latency_ms: int,
        response_expected: bool,
        confidence: float,
        response_source: str = "llm",
        save: bool = True,
    ) -> None:
        self.events.append(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "session_id": self.session_id,
                "outcome": self._safe_label(outcome),
                "reason": self._safe_label(reason),
                "latency_ms": max(int(latency_ms), 0),
                "response_expected": bool(response_expected),
                "confidence": round(max(0.0, min(float(confidence), 1.0)), 4),
                "response_source": self._safe_label(response_source),
            }
        )
        self.events = self.events[-self.MAX_EVENTS :]
        if save:
            self.save()

    def clear(self) -> int:
        count = len(self.events)
        self.events.clear()
        self.save()
        return count

    def start_new_session(self) -> None:
        self.session_id = uuid4().hex

    def selected_events(self, current_session_only: bool) -> list[dict[str, object]]:
        if not current_session_only:
            return list(self.events)
        return [
            event
            for event in self.events
            if event.get("session_id") == self.session_id
        ]

    def summary(self, current_session_only: bool = True) -> dict[str, object]:
        events = self.selected_events(current_session_only)
        outcomes = Counter(str(event.get("outcome", "unknown")) for event in events)
        latencies = [int(event.get("latency_ms", 0)) for event in events]
        sources = Counter(
            str(event.get("response_source", "llm")) for event in events
        )
        return {
            "total": len(events),
            "sent": outcomes["sent"],
            "ignored": outcomes["ignored"],
            "missed": outcomes["missed"],
            "blocked": outcomes["blocked"],
            "failed": outcomes["failed"],
            "llm": sources["llm"],
            "rivescript": sources["rivescript"],
            "average_latency_ms": (
                round(sum(latencies) / len(latencies)) if latencies else 0
            ),
        }

    @staticmethod
    def _safe_label(value: str) -> str:
        clean = "".join(
            character
            for character in str(value).casefold().replace(" ", "_")
            if character.isalnum() or character in {"_", "-"}
        )
        return clean[:80] or "unknown"
