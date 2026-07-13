from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.json_store import atomic_write_json, load_json_with_backup
from core.migrations import migrate_payload
from core.paths import user_data_root


@dataclass(slots=True)
class StreamSession:
    started_at: str
    ended_at: str = ""
    peak_viewers: int = 0
    messages: int = 0
    follows: int = 0
    subscriptions: int = 0
    cheers: int = 0
    raids: int = 0

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> StreamSession:
        return cls(
            started_at=str(values.get("started_at", "")),
            ended_at=str(values.get("ended_at", "")),
            peak_viewers=max(int(values.get("peak_viewers", 0)), 0),
            messages=max(int(values.get("messages", 0)), 0),
            follows=max(int(values.get("follows", 0)), 0),
            subscriptions=max(int(values.get("subscriptions", 0)), 0),
            cheers=max(int(values.get("cheers", 0)), 0),
            raids=max(int(values.get("raids", 0)), 0),
        )


class StreamSessionStore:
    LIMIT = 100

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "memory" / "stream_sessions.json"
        self.sessions: list[StreamSession] = []
        self.current: StreamSession | None = None
        self.dirty = False
        self.retention_days = 365

    def load(self) -> None:
        if not self.path.exists():
            return
        values = load_json_with_backup(self.path)
        if not isinstance(values, dict):
            raise ValueError("Stream session history must be an object.")
        values = migrate_payload("sessions", values)
        raw_sessions = values.get("sessions", [])
        if not isinstance(raw_sessions, list):
            raise ValueError("Stream sessions must be a list.")
        self.sessions = [
            StreamSession.from_dict(value)
            for value in raw_sessions[: self.LIMIT]
            if isinstance(value, dict)
        ]
        current = values.get("current")
        self.current = (
            StreamSession.from_dict(current)
            if isinstance(current, dict)
            else None
        )
        retention_days = values.get("retention_days", 365)
        self.retention_days = (
            min(max(int(retention_days), 30), 3650)
            if not isinstance(retention_days, bool)
            else 365
        )
        self.dirty = False

    def save(self) -> None:
        if not self.dirty:
            return
        payload = {
            "version": 1,
            "current": asdict(self.current) if self.current else None,
            "retention_days": self.retention_days,
            "sessions": [asdict(session) for session in self.sessions],
        }
        atomic_write_json(self.path, payload)
        self.dirty = False

    def prune(
        self,
        retention_days: int,
        now: datetime | None = None,
    ) -> int:
        self.retention_days = min(max(int(retention_days), 30), 3650)
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(
            days=self.retention_days
        )
        retained: list[StreamSession] = []
        for session in self.sessions:
            value = session.ended_at or session.started_at
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                retained.append(session)
                continue
            if timestamp >= cutoff:
                retained.append(session)
        removed = len(self.sessions) - len(retained)
        self.sessions = retained
        self.dirty = True
        self.save()
        return removed


class StreamSessionTracker:
    def __init__(self, store: StreamSessionStore) -> None:
        self.store = store

    def observe_stream(self, stream: dict | None) -> bool:
        changed = False
        if isinstance(stream, dict):
            started_at = str(stream.get("started_at", ""))
            if self.store.current is None:
                self.store.current = StreamSession(
                    started_at=started_at
                    or datetime.now(timezone.utc).isoformat()
                )
                changed = True
            viewers = max(int(stream.get("viewer_count", 0)), 0)
            if viewers > self.store.current.peak_viewers:
                self.store.current.peak_viewers = viewers
                changed = True
        elif self.store.current is not None:
            self.store.current.ended_at = datetime.now(timezone.utc).isoformat()
            self.store.sessions.insert(0, self.store.current)
            del self.store.sessions[self.store.LIMIT :]
            self.store.current = None
            changed = True
        if changed:
            self.store.dirty = True
            self.store.save()
        return changed

    def observe_message(self) -> bool:
        return self._increment("messages")

    def observe_event(self, event_type: str) -> bool:
        field_by_type = {
            "channel.follow": "follows",
            "channel.subscribe": "subscriptions",
            "channel.subscription.gift": "subscriptions",
            "channel.subscription.message": "subscriptions",
            "channel.cheer": "cheers",
            "channel.raid": "raids",
        }
        field_name = field_by_type.get(event_type)
        return self._increment(field_name) if field_name else False

    def _increment(self, field_name: str) -> bool:
        if self.store.current is None:
            return False
        setattr(
            self.store.current,
            field_name,
            int(getattr(self.store.current, field_name)) + 1,
        )
        self.store.dirty = True
        return True
