from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from products.hub.core.migrations import migrate_payload
from shared.streamhouse_runtime.paths import user_data_root


LOCAL_CHATTER_GROUPS = frozenset({"", "Regulars", "Bots", "Viewers"})


def _normalize_manual_group(value: Any) -> str:
    group = str(value).strip()
    return group if group in LOCAL_CHATTER_GROUPS else ""


def _normalize_memory(values: dict[str, Any]) -> dict[str, Any]:
    """Fill structured-memory fields on legacy and current records."""
    memory = dict(values)
    source = str(memory.get("source", "manual"))
    memory["source"] = source
    memory.setdefault("status", "approved" if source == "manual" else "pending")
    memory.setdefault("confidence", 1.0 if source == "manual" else 0.5)
    memory.setdefault("evidence", [])
    memory.setdefault("key", "")
    memory.setdefault("last_confirmed_at", memory.get("created_at", ""))
    memory.setdefault("conflicts_with", "")
    memory.setdefault("rejection_reason", "")
    memory.setdefault("pinned", False)
    memory.setdefault("archived", False)
    return memory


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
    memory_enabled: bool = False
    manual_group: str = ""
    memory_consent: str = "unknown"
    memory_consented_at: str = ""
    memory_consent_version: str = ""
    memory_stream_ids: list[str] = field(default_factory=list)
    daily_memory: list[dict[str, str]] = field(default_factory=list)
    daily_memory_updated_at: str = ""
    daily_memory_stream_id: str = ""

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
                _normalize_memory(memory)
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
            memory_enabled=(
                bool(values.get("memory_enabled", False))
                and values.get("memory_consent") == "opted_in"
            ),
            manual_group=_normalize_manual_group(values.get("manual_group", "")),
            memory_consent=str(values.get("memory_consent", "unknown")),
            memory_consented_at=str(values.get("memory_consented_at", "")),
            memory_consent_version=str(values.get("memory_consent_version", "")),
            memory_stream_ids=list(
                dict.fromkeys(
                    str(value)
                    for value in values.get("memory_stream_ids", [])
                    if str(value)
                )
            )[-100:],
            daily_memory=[
                {
                    "speaker": str(item.get("speaker", "viewer"))[:20],
                    "viewer": str(item.get("viewer", ""))[:100],
                    "message": str(item.get("message", ""))[:500],
                    "timestamp": str(item.get("timestamp", ""))[:50],
                }
                for item in values.get("daily_memory", [])[-100:]
                if isinstance(item, dict) and str(item.get("message", "")).strip()
            ],
            daily_memory_updated_at=str(values.get("daily_memory_updated_at", "")),
            daily_memory_stream_id=str(values.get("daily_memory_stream_id", "")),
        )


class ChatterHistoryStore:
    """Persist lightweight, local-only Twitch chatter participation data."""

    REGULAR_ACTIVE_DAYS = 5
    REGULAR_MESSAGES = 25
    REGULAR_SNAPSHOT_DAYS = 10
    MEMORY_REGULAR_STREAMS = 5
    MEMORY_CONSENT_VERSION = "1"

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
        loaded: dict[str, ChatterRecord] = {}
        normalized = False
        for raw_user_id, raw_record in records.items():
            user_id = str(raw_user_id).strip()
            if not isinstance(raw_record, dict) or not user_id:
                normalized = True
                continue
            record = ChatterRecord.from_dict(raw_record)
            if record.user_id != user_id:
                normalized = True
            record.user_id = user_id
            if str(raw_record.get("manual_group", "")).strip() != record.manual_group:
                normalized = True
            loaded[user_id] = record
        self.records = loaded
        # Rewriting removes unconsented activity-only profiles while retaining
        # Hub-owned group assignments, observed bots, and explicit consent.
        self.dirty = normalized or any(
            record.memory_consent == "unknown"
            and not record.is_bot
            and not record.manual_group
            for record in self.records.values()
        )

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
        if not self.can_create_keynotes(user_id):
            raise PermissionError(
                "Persistent keynotes require consent and five opted-in streams."
            )
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
            "status": "approved",
            "confidence": 1.0,
            "evidence": [],
            "key": "",
            "last_confirmed_at": now,
            "conflicts_with": "",
            "rejection_reason": "",
        }
        if not memory["text"]:
            raise ValueError("Memory text is required.")
        record.memories.append(memory)
        self.dirty = True
        return memory

    def propose_memory(
        self,
        user_id: str,
        text: str,
        category: str = "General",
        *,
        confidence: float = 0.5,
        evidence: Iterable[dict[str, Any]] = (),
        key: str = "",
        source: str = "ai",
    ) -> dict[str, Any]:
        """Queue an extracted memory, merging exact duplicates when possible."""
        record = self.records[user_id]
        if not self.can_create_keynotes(user_id):
            raise PermissionError(
                "Persistent keynotes require consent and five opted-in streams."
            )
        clean_text = text.strip()[:1000]
        if not clean_text:
            raise ValueError("Memory text is required.")
        clean_key = key.strip().casefold()[:100]
        normalized = " ".join(clean_text.casefold().split())
        now = datetime.now(timezone.utc).isoformat()
        clean_evidence = [
            {
                "text": str(item.get("text", ""))[:500],
                "timestamp": str(item.get("timestamp", ""))[:50],
                "message_id": str(item.get("message_id", ""))[:100],
            }
            for item in evidence
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ][:20]
        conflict_id = ""
        for existing in record.memories:
            existing_text = " ".join(
                str(existing.get("text", "")).casefold().split()
            )
            if existing_text == normalized:
                prior = list(existing.get("evidence", []))
                existing["evidence"] = (prior + clean_evidence)[-20:]
                existing["confidence"] = max(
                    float(existing.get("confidence", 0.0)),
                    max(0.0, min(float(confidence), 1.0)),
                )
                existing["last_confirmed_at"] = now
                existing["updated_at"] = now
                self.dirty = True
                return existing
            if (
                clean_key
                and clean_key == str(existing.get("key", "")).casefold()
                and existing.get("status") != "rejected"
            ):
                conflict_id = str(existing.get("id", ""))
        memory = {
            "id": uuid4().hex,
            "text": clean_text,
            "category": category.strip()[:50] or "General",
            "source": source.strip()[:50] or "ai",
            "created_at": now,
            "updated_at": now,
            "last_confirmed_at": now,
            "pinned": False,
            "archived": False,
            "status": "pending",
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "evidence": clean_evidence,
            "key": clean_key,
            "conflicts_with": conflict_id,
            "rejection_reason": "",
        }
        record.memories.append(memory)
        self.dirty = True
        return memory

    def review_memory(
        self,
        user_id: str,
        memory_id: str,
        approved: bool,
        rejection_reason: str = "",
    ) -> dict[str, Any]:
        memory = self.get_memory(user_id, memory_id)
        memory["status"] = "approved" if approved else "rejected"
        memory["rejection_reason"] = (
            "" if approved else rejection_reason.strip()[:500]
        )
        memory["updated_at"] = datetime.now(timezone.utc).isoformat()
        conflict_id = str(memory.get("conflicts_with", ""))
        if approved and conflict_id:
            try:
                replaced = self.get_memory(user_id, conflict_id)
            except KeyError:
                pass
            else:
                replaced["status"] = "superseded"
                replaced["archived"] = True
                replaced["updated_at"] = memory["updated_at"]
            memory["conflicts_with"] = ""
        self.dirty = True
        return memory

    def set_memory_enabled(self, user_id: str, enabled: bool) -> None:
        record = self.records[user_id]
        # Broadcasters may pause consented memory, but cannot grant consent for
        # a viewer from the local UI.
        record.memory_enabled = bool(enabled) and self.has_memory_consent(user_id)
        if not enabled:
            for memory in record.memories:
                if memory.get("status") == "pending":
                    memory["status"] = "rejected"
                    memory["rejection_reason"] = "Viewer memory disabled"
        self.dirty = True

    def has_memory_consent(self, user_id: str) -> bool:
        record = self.records.get(user_id)
        return bool(record and record.memory_consent == "opted_in")

    def opt_in_memory(
        self,
        user_id: str,
        user_name: str,
        *,
        consented_at: datetime | None = None,
    ) -> ChatterRecord:
        """Record explicit consent without retaining pre-consent observations."""
        when = consented_at or datetime.now(timezone.utc)
        record = self.records.get(user_id)
        if record is None:
            record = self._observe(user_id, user_name, when)
        assert record is not None
        if record.memory_consent != "opted_in":
            timestamp = when.astimezone(timezone.utc).isoformat()
            day = when.astimezone(timezone.utc).date().isoformat()
            record.first_seen = timestamp
            record.last_seen = timestamp
            record.active_days = [day]
            record.message_count = 0
            record.snapshot_days = 0
            record.last_snapshot_day = ""
            record.followed_at = ""
            record.memories.clear()
            record.tags.clear()
            record.private_notes = ""
            record.session_messages.clear()
            record.timeline.clear()
            record.role_history.clear()
            record.memory_stream_ids.clear()
            record.daily_memory.clear()
            record.daily_memory_updated_at = ""
            record.daily_memory_stream_id = ""
        record.user_name = user_name or record.user_name
        record.memory_consent = "opted_in"
        record.memory_consented_at = when.astimezone(timezone.utc).isoformat()
        record.memory_consent_version = self.MEMORY_CONSENT_VERSION
        record.memory_enabled = True
        self.dirty = True
        return record

    def opt_out_memory(self, user_id: str) -> None:
        """Stop memory and erase content, retaining only the opt-out preference."""
        record = self.records[user_id]
        record.memory_consent = "opted_out"
        record.memory_enabled = False
        record.memory_consented_at = ""
        record.memory_consent_version = ""
        record.memory_stream_ids.clear()
        record.memories.clear()
        record.daily_memory.clear()
        record.daily_memory_updated_at = ""
        record.daily_memory_stream_id = ""
        record.tags.clear()
        record.private_notes = ""
        record.active_days.clear()
        record.message_count = 0
        record.snapshot_days = 0
        record.last_snapshot_day = ""
        record.followed_at = ""
        record.session_messages.clear()
        record.timeline.clear()
        record.role_history.clear()
        record.roles.clear()
        record.manual_group = ""
        record.first_seen = ""
        record.last_seen = ""
        self.dirty = True

    def delete_viewer_data(self, user_id: str) -> bool:
        """Erase the complete local record, including consent metadata."""
        removed = self.records.pop(user_id, None) is not None
        self.dirty = self.dirty or removed
        return removed

    def record_memory_stream(self, user_id: str, stream_id: str) -> int:
        record = self.records[user_id]
        if not self.has_memory_consent(user_id) or not stream_id:
            return len(record.memory_stream_ids)
        if stream_id not in record.memory_stream_ids:
            record.memory_stream_ids.append(stream_id[:200])
            record.memory_stream_ids = record.memory_stream_ids[-100:]
            self.dirty = True
        return len(record.memory_stream_ids)

    def can_create_keynotes(self, user_id: str) -> bool:
        record = self.records.get(user_id)
        return bool(
            record
            and record.memory_enabled
            and self.has_memory_consent(user_id)
            and len(record.memory_stream_ids) >= self.MEMORY_REGULAR_STREAMS
        )

    def record_daily_memory(
        self,
        user_id: str,
        *,
        speaker: str,
        viewer: str,
        message: str,
        timestamp: datetime | None = None,
        stream_id: str = "",
    ) -> None:
        if not self.has_memory_consent(user_id):
            return
        clean = " ".join(message.strip().split())[:500]
        if not clean:
            return
        when = timestamp or datetime.now(timezone.utc)
        record = self.records[user_id]
        record.daily_memory.append(
            {
                "speaker": speaker[:20],
                "viewer": viewer[:100],
                "message": clean,
                "timestamp": when.astimezone(timezone.utc).isoformat(),
            }
        )
        record.daily_memory = record.daily_memory[-100:]
        record.daily_memory_updated_at = when.astimezone(timezone.utc).isoformat()
        record.daily_memory_stream_id = stream_id[:200]
        self.dirty = True

    def expire_daily_memories(
        self,
        *,
        now: datetime | None = None,
        reset_at: time = time(4, 0),
        active_stream_id: str = "",
    ) -> int:
        """Clear old context unless it belongs to the stream still live."""
        current = (now or datetime.now().astimezone()).astimezone()
        boundary = current.replace(
            hour=reset_at.hour,
            minute=reset_at.minute,
            second=0,
            microsecond=0,
        )
        if current < boundary:
            boundary -= timedelta(days=1)
        removed = 0
        for record in self.records.values():
            if not record.daily_memory or not record.daily_memory_updated_at:
                continue
            try:
                updated = datetime.fromisoformat(
                    record.daily_memory_updated_at.replace("Z", "+00:00")
                ).astimezone()
            except ValueError:
                updated = datetime.min.replace(tzinfo=current.tzinfo)
            same_live_stream = bool(
                active_stream_id
                and record.daily_memory_stream_id == active_stream_id
            )
            if updated < boundary and not same_live_stream:
                removed += len(record.daily_memory)
                record.daily_memory.clear()
                record.daily_memory_updated_at = ""
                record.daily_memory_stream_id = ""
                self.dirty = True
        return removed

    def approved_memories(self, user_id: str) -> list[dict[str, Any]]:
        record = self.records[user_id]
        if not record.memory_enabled:
            return []
        return [
            memory
            for memory in record.memories
            if memory.get("status", "approved") == "approved"
            and not bool(memory.get("archived", False))
        ]

    def viewer_summary(self, user_id: str) -> str:
        record = self.records[user_id]
        groups = list(record.roles)
        if self.is_regular(user_id):
            groups.append("Regular")
        memories = self.approved_memories(user_id)
        facts = "; ".join(str(item.get("text", "")) for item in memories[:6])
        summary = (
            f"{record.user_name or record.user_id} is a "
            f"{', '.join(groups) or 'viewer'} with {record.message_count} observed "
            f"messages across {len(record.active_days)} active day(s)."
        )
        return f"{summary} Known context: {facts}." if facts else summary

    def relevant_memories(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        terms = {term for term in query.casefold().split() if len(term) > 2}
        ranked = []
        for memory in self.approved_memories(user_id):
            text_terms = set(str(memory.get("text", "")).casefold().split())
            overlap = len(terms & text_terms)
            score = overlap * 10 + int(bool(memory.get("pinned"))) * 5
            score += float(memory.get("confidence", 0.0))
            if not terms or overlap or memory.get("pinned"):
                ranked.append((score, memory))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [memory for _score, memory in ranked[: max(limit, 0)]]

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
        if not target.manual_group:
            target.manual_group = source.manual_group
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
        return bool(record and (record.is_bot or record.manual_group == "Bots"))

    def set_manual_group(self, user_id: str, group: str) -> None:
        """Override a chatter's local display group without changing Twitch roles."""
        if group not in LOCAL_CHATTER_GROUPS:
            raise ValueError(f"Unsupported local chatter group: {group}")
        record = self.records[user_id]
        record.manual_group = group
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        payload = {
            "version": 6,
            "chatters": {
                user_id: asdict(record)
                for user_id, record in self.records.items()
                if record.memory_consent in {"opted_in", "opted_out"}
                or record.is_bot
                or record.manual_group
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
        elif record.memory_consent == "opted_out":
            return None
        record.user_name = user_name or record.user_name
        record.last_seen = timestamp
        if day not in record.active_days:
            record.active_days.append(day)
            record.active_days = record.active_days[-90:]
        self.dirty = True
        return record
