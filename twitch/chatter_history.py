from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from core.json_store import atomic_write_json, load_json_with_backup
from core.migrations import migrate_payload
from core.paths import user_data_root


@dataclass(slots=True)
class ChatterRecord:
    user_id: str
    user_name: str
    first_seen: str
    last_seen: str
    active_days: list[str] = field(default_factory=list)
    message_count: int = 0
    snapshot_days: int = 0
    last_snapshot_day: str = ""
    is_bot: bool = False
    roles: list[str] = field(default_factory=list)
    followed_at: str = ""
    memories: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    private_notes: str = ""
    session_messages: dict[str, int] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    role_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ChatterRecord:
        return cls(
            user_id=str(values.get("user_id", "")),
            user_name=str(values.get("user_name", "")),
            first_seen=str(values.get("first_seen", "")),
            last_seen=str(values.get("last_seen", "")),
            active_days=[str(day) for day in values.get("active_days", [])][
                -90:
            ],
            message_count=max(int(values.get("message_count", 0)), 0),
            snapshot_days=max(int(values.get("snapshot_days", 0)), 0),
            last_snapshot_day=str(values.get("last_snapshot_day", "")),
            is_bot=bool(values.get("is_bot", False)),
            roles=[str(role) for role in values.get("roles", [])],
            followed_at=str(values.get("followed_at", "")),
            memories=[
                dict(memory)
                for memory in values.get("memories", [])
                if isinstance(memory, dict)
            ],
            tags=[str(tag) for tag in values.get("tags", [])][:50],
            private_notes=str(values.get("private_notes", ""))[:5000],
            session_messages={
                str(session_id): max(int(count), 0)
                for session_id, count in values.get(
                    "session_messages", {}
                ).items()
            }
            if isinstance(values.get("session_messages", {}), dict)
            else {},
            timeline=[
                dict(item)
                for item in values.get("timeline", [])[-200:]
                if isinstance(item, dict)
            ],
            role_history=[
                dict(item)
                for item in values.get("role_history", [])[-100:]
                if isinstance(item, dict)
            ],
        )


class ChatterHistoryStore:
    """Persist lightweight, local-only Twitch chatter participation data."""

    REGULAR_ACTIVE_DAYS = 5
    REGULAR_MESSAGES = 25
    REGULAR_SNAPSHOT_DAYS = 10

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "memory" / "twitch_chatters.json"
        self.records: dict[str, ChatterRecord] = {}
        self.dirty = False

    def load(self) -> None:
        if not self.path.exists():
            return
        values = load_json_with_backup(self.path)
        if not isinstance(values, dict):
            raise ValueError("Chatter history must contain a JSON object.")
        values = migrate_payload("chatters", values)
        records = values.get("chatters", {})
        if not isinstance(records, dict):
            raise ValueError("Chatter history chatters must be an object.")
        self.records = {
            str(user_id): ChatterRecord.from_dict(record)
            for user_id, record in records.items()
            if isinstance(record, dict) and str(user_id)
        }
        self.dirty = False

    def observe_message(
        self,
        user_id: str,
        user_name: str,
        observed_at: datetime | None = None,
        is_bot: bool = False,
        session_id: str = "",
    ) -> None:
        record = self._observe(user_id, user_name, observed_at)
        if record is not None:
            record.message_count += 1
            record.is_bot = record.is_bot or is_bot
            if session_id:
                record.session_messages[session_id] = (
                    record.session_messages.get(session_id, 0) + 1
                )
            self.dirty = True

    def observe_snapshot(
        self,
        chatters: Iterable[dict],
        observed_at: datetime | None = None,
        moderator_ids: set[str] | frozenset[str] = frozenset(),
        vip_ids: set[str] | frozenset[str] = frozenset(),
        subscriber_ids: set[str] | frozenset[str] = frozenset(),
        session_id: str = "",
    ) -> None:
        when = observed_at or datetime.now(timezone.utc)
        day = when.astimezone(timezone.utc).date().isoformat()
        for chatter in chatters:
            record = self._observe(
                str(chatter.get("user_id", "")),
                str(chatter.get("user_name", "")),
                when,
            )
            if record is not None and record.last_snapshot_day != day:
                record.snapshot_days += 1
                record.last_snapshot_day = day
                self.dirty = True
            if record is not None and session_id:
                record.session_messages.setdefault(session_id, 0)
            if record is not None:
                roles: list[str] = []
                if record.user_id in moderator_ids:
                    roles.append("Moderator")
                if record.user_id in vip_ids:
                    roles.append("VIP")
                if record.user_id in subscriber_ids:
                    roles.append("Subscriber")
                if record.is_bot:
                    roles.append("Bot")
                if roles != record.roles:
                    previous_roles = list(record.roles)
                    record.roles = roles
                    changed_at = when.astimezone(timezone.utc).isoformat()
                    role_change = {
                        "timestamp": changed_at,
                        "from": previous_roles,
                        "to": list(roles),
                    }
                    record.role_history.append(role_change)
                    record.role_history = record.role_history[-100:]
                    self._append_timeline(
                        record,
                        "role_change",
                        "Roles changed from "
                        f"{', '.join(previous_roles) or 'Viewer'} to "
                        f"{', '.join(roles) or 'Viewer'}",
                        changed_at,
                    )
                    self.dirty = True

    def record_follow(
        self,
        user_id: str,
        user_name: str,
        followed_at: str,
    ) -> None:
        record = self._observe(user_id, user_name, None)
        if record is not None and followed_at:
            record.followed_at = followed_at
            self.dirty = True

    def add_memory(
        self,
        user_id: str,
        text: str,
        category: str = "General",
    ) -> dict[str, Any]:
        record = self.records[user_id]
        now = datetime.now(timezone.utc).isoformat()
        memory = {
            "id": uuid4().hex,
            "text": text.strip()[:1000],
            "category": category.strip()[:50] or "General",
            "source": "manual",
            "created_at": now,
            "updated_at": now,
            "pinned": False,
            "archived": False,
        }
        if not memory["text"]:
            raise ValueError("Memory text is required.")
        record.memories.append(memory)
        self.dirty = True
        return memory

    def update_memory(
        self,
        user_id: str,
        memory_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        memory = self.get_memory(user_id, memory_id)
        if "text" in changes:
            text = str(changes["text"]).strip()[:1000]
            if not text:
                raise ValueError("Memory text is required.")
            memory["text"] = text
        if "category" in changes:
            memory["category"] = (
                str(changes["category"]).strip()[:50] or "General"
            )
        for field_name in ("pinned", "archived"):
            if field_name in changes:
                memory[field_name] = bool(changes[field_name])
        memory["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.dirty = True
        return memory

    def delete_memory(self, user_id: str, memory_id: str) -> None:
        record = self.records[user_id]
        memory = self.get_memory(user_id, memory_id)
        record.memories.remove(memory)
        self.dirty = True

    def get_memory(
        self,
        user_id: str,
        memory_id: str,
    ) -> dict[str, Any]:
        for memory in self.records[user_id].memories:
            if str(memory.get("id", "")) == memory_id:
                return memory
        raise KeyError(memory_id)

    def clear_memories(self, user_id: str) -> None:
        self.records[user_id].memories.clear()
        self.dirty = True

    def update_profile(
        self,
        user_id: str,
        tags: Iterable[str],
        private_notes: str,
    ) -> None:
        record = self.records[user_id]
        record.tags = list(
            dict.fromkeys(
                tag.strip()[:50]
                for tag in tags
                if tag.strip()
            )
        )[:50]
        record.private_notes = private_notes.strip()[:5000]
        self.dirty = True

    def record_event(
        self,
        user_id: str,
        user_name: str,
        event_type: str,
        text: str,
        occurred_at: datetime,
        session_id: str = "",
    ) -> None:
        record = self._observe(user_id, user_name, occurred_at)
        if record is None:
            return
        self._append_timeline(
            record,
            event_type,
            text,
            occurred_at.astimezone(timezone.utc).isoformat(),
            session_id,
        )
        self.dirty = True

    def merge_records(self, source_user_id: str, target_user_id: str) -> None:
        if source_user_id == target_user_id:
            raise ValueError("Choose two different viewers.")
        source = self.records[source_user_id]
        target = self.records[target_user_id]
        target.first_seen = min(target.first_seen, source.first_seen)
        target.last_seen = max(target.last_seen, source.last_seen)
        target.active_days = sorted(
            set(target.active_days) | set(source.active_days)
        )[-90:]
        target.message_count += source.message_count
        target.snapshot_days += source.snapshot_days
        target.is_bot = target.is_bot or source.is_bot
        target.tags = list(dict.fromkeys(target.tags + source.tags))[:50]
        if source.private_notes:
            separator = "\n\n" if target.private_notes else ""
            target.private_notes = (
                target.private_notes + separator + source.private_notes
            )[:5000]
        target.memories.extend(source.memories)
        target.timeline = sorted(
            target.timeline + source.timeline,
            key=lambda item: str(item.get("timestamp", "")),
        )[-200:]
        target.role_history = sorted(
            target.role_history + source.role_history,
            key=lambda item: str(item.get("timestamp", "")),
        )[-100:]
        for session_id, count in source.session_messages.items():
            target.session_messages[session_id] = (
                target.session_messages.get(session_id, 0) + count
            )
        del self.records[source_user_id]
        self.dirty = True

    @staticmethod
    def engagement_streak(active_days: Iterable[str]) -> int:
        parsed = []
        for value in set(active_days):
            try:
                parsed.append(datetime.fromisoformat(value).date())
            except ValueError:
                continue
        if not parsed:
            return 0
        parsed.sort(reverse=True)
        streak = 1
        for newer, older in zip(parsed, parsed[1:]):
            if (newer - older).days != 1:
                break
            streak += 1
        return streak

    @staticmethod
    def _append_timeline(
        record: ChatterRecord,
        event_type: str,
        text: str,
        timestamp: str,
        session_id: str = "",
    ) -> None:
        record.timeline.append(
            {
                "id": uuid4().hex,
                "type": event_type,
                "text": text[:500],
                "timestamp": timestamp,
                "session_id": session_id,
            }
        )
        record.timeline = record.timeline[-200:]

    def is_regular(self, user_id: str) -> bool:
        record = self.records.get(user_id)
        if record is None:
            return False
        return len(record.active_days) >= self.REGULAR_ACTIVE_DAYS and (
            record.message_count >= self.REGULAR_MESSAGES
            or record.snapshot_days >= self.REGULAR_SNAPSHOT_DAYS
        )

    def is_bot(self, user_id: str) -> bool:
        record = self.records.get(user_id)
        return bool(record and record.is_bot)

    def save(self) -> None:
        if not self.dirty:
            return
        payload = {
            "version": 3,
            "chatters": {
                user_id: asdict(record)
                for user_id, record in self.records.items()
            },
        }
        atomic_write_json(self.path, payload)
        self.dirty = False

    def _observe(
        self,
        user_id: str,
        user_name: str,
        observed_at: datetime | None,
    ) -> ChatterRecord | None:
        if not user_id:
            return None
        when = observed_at or datetime.now(timezone.utc)
        timestamp = when.astimezone(timezone.utc).isoformat()
        day = when.astimezone(timezone.utc).date().isoformat()
        record = self.records.get(user_id)
        if record is None:
            record = ChatterRecord(
                user_id=user_id,
                user_name=user_name,
                first_seen=timestamp,
                last_seen=timestamp,
            )
            self.records[user_id] = record
        record.user_name = user_name or record.user_name
        record.last_seen = timestamp
        if day not in record.active_days:
            record.active_days.append(day)
            record.active_days = record.active_days[-90:]
        self.dirty = True
        return record
