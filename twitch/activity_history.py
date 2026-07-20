from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.json_store import atomic_write_json, load_json_with_backup
from core.migrations import migrate_payload
from core.paths import user_data_root


@dataclass(frozen=True, slots=True)
class PersistedActivity:
    category: str
    text: str
    color: str
    occurred_at: str
    user_id: str = ""

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> PersistedActivity:
        occurred_at = str(values.get("occurred_at", ""))
        datetime.fromisoformat(occurred_at)
        return cls(
            category=str(values.get("category", "Other"))[:50],
            text=str(values.get("text", ""))[:500],
            color=str(values.get("color", "#adadb8"))[:20],
            occurred_at=occurred_at,
            user_id=str(values.get("user_id", ""))[:100],
        )

    def age_text(self, now: datetime | None = None) -> str:
        occurred = datetime.fromisoformat(self.occurred_at)
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        elapsed_seconds = max(
            int((current - occurred).total_seconds()),
            0,
        )
        if elapsed_seconds < 60:
            age = "just now"
        elif elapsed_seconds < 60 * 60:
            age = f"{elapsed_seconds // 60}m ago"
        elif elapsed_seconds < 24 * 60 * 60:
            age = f"{elapsed_seconds // (60 * 60)}h ago"
        elif elapsed_seconds <= 7 * 24 * 60 * 60:
            age = f"{elapsed_seconds // (24 * 60 * 60)}d ago"
        else:
            age = f"{elapsed_seconds // (7 * 24 * 60 * 60)}w ago"
        return age

    def display_text(self, now: datetime | None = None) -> str:
        return f"{self.text}  -  {self.age_text(now)}"


class ActivityHistoryStore:
    LIMIT = 200
    MINUTE_MS = 60_000
    HOUR_MS = 60 * MINUTE_MS
    DAY_MS = 24 * HOUR_MS
    WEEK_MS = 7 * DAY_MS

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "memory" / "twitch_activity.json"
        self.entries: list[PersistedActivity] = []

    def load(self) -> list[PersistedActivity]:
        if not self.path.exists():
            self.entries = []
            return []
        values = load_json_with_backup(self.path)
        if not isinstance(values, dict):
            raise ValueError("Activity history must contain a JSON object.")
        values = migrate_payload("activity", values)
        raw_entries = values.get("events", [])
        if not isinstance(raw_entries, list):
            raise ValueError("Activity history events must be a list.")
        entries: list[PersistedActivity] = []
        for value in raw_entries[: self.LIMIT]:
            if not isinstance(value, dict):
                continue
            try:
                entry = PersistedActivity.from_dict(value)
            except (TypeError, ValueError):
                continue
            if entry.text:
                entries.append(entry)
        self.entries = entries
        return list(entries)

    def add(self, entry: PersistedActivity) -> None:
        self.entries.insert(0, entry)
        del self.entries[self.LIMIT :]
        self.save()

    def delete_user(self, user_id: str, user_name: str = "") -> int:
        """Remove activity associated with a viewer, including legacy name-only rows."""
        clean_id = user_id.strip()
        clean_name = user_name.strip().casefold()
        retained = [
            entry
            for entry in self.entries
            if not (
                (clean_id and entry.user_id == clean_id)
                or (
                    clean_name
                    and not entry.user_id
                    and entry.text.casefold().startswith(clean_name + " ")
                )
            )
        ]
        removed = len(self.entries) - len(retained)
        if removed:
            self.entries[:] = retained
            self.save()
        return removed

    def refresh_interval_ms(self, now: datetime | None = None) -> int | None:
        if not self.entries:
            return None
        occurred = datetime.fromisoformat(self.entries[0].occurred_at)
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        elapsed_seconds = max((current - occurred).total_seconds(), 0)
        if elapsed_seconds < 60 * 60:
            return self.MINUTE_MS
        if elapsed_seconds < 24 * 60 * 60:
            return self.HOUR_MS
        if elapsed_seconds <= 7 * 24 * 60 * 60:
            return self.DAY_MS
        return self.WEEK_MS

    def save(self) -> None:
        payload = {
            "version": 1,
            "events": [asdict(entry) for entry in self.entries],
        }
        atomic_write_json(self.path, payload)
