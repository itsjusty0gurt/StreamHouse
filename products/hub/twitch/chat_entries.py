from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from products.hub.twitch.models import TwitchChatNotice, TwitchMessage


class TwitchChatEntryType(StrEnum):
    """Presentation-independent kinds of entries shown in Hub chat."""

    MESSAGE = "message"
    TWITCH_EVENT = "twitch_event"
    MODERATION = "moderation"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class TwitchChatEntry:
    """One bounded chat-timeline item with its original Twitch metadata."""

    entry_id: str
    kind: TwitchChatEntryType
    received_at: datetime
    text: str
    message: TwitchMessage | None = None
    notice: TwitchChatNotice | None = None
    deleted: bool = False

    @classmethod
    def from_message(cls, message: TwitchMessage) -> TwitchChatEntry:
        stable_id = message.message_id or uuid4().hex
        return cls(
            entry_id=f"message-{stable_id}",
            kind=TwitchChatEntryType.MESSAGE,
            received_at=message.received_at,
            text=message.text,
            message=message,
        )

    @classmethod
    def from_notice(cls, notice: TwitchChatNotice) -> TwitchChatEntry:
        moderation_kinds = {"clear", "clear_user", "delete", "ban", "timeout"}
        return cls(
            entry_id=f"notice-{uuid4().hex}",
            kind=(
                TwitchChatEntryType.MODERATION
                if notice.kind in moderation_kinds
                else TwitchChatEntryType.TWITCH_EVENT
            ),
            received_at=notice.received_at,
            text=notice.text,
            notice=notice,
        )

    @classmethod
    def system(cls, text: str, received_at: datetime) -> TwitchChatEntry:
        return cls(
            entry_id=f"system-{uuid4().hex}",
            kind=TwitchChatEntryType.SYSTEM,
            received_at=received_at,
            text=text,
        )

    @property
    def user_id(self) -> str:
        return self.message.user_id if self.message is not None else ""

    @property
    def username(self) -> str:
        return self.message.username if self.message is not None else ""

    @property
    def display_name(self) -> str:
        return self.username

    @property
    def user_login(self) -> str:
        return self.message.user_login if self.message is not None else ""

    @property
    def message_id(self) -> str:
        return self.message.message_id if self.message is not None else ""


class TwitchChatHistory:
    """Small in-memory session history; persistent Twitch data stays elsewhere."""

    def __init__(self, limit: int = 1000) -> None:
        self._limit = max(1, int(limit))
        self._entries: list[TwitchChatEntry] = []

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def entries(self) -> tuple[TwitchChatEntry, ...]:
        return tuple(self._entries)

    def set_limit(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        del self._entries[: max(0, len(self._entries) - self._limit)]

    def add(self, entry: TwitchChatEntry) -> tuple[TwitchChatEntry, ...]:
        self._entries.append(entry)
        overflow = max(0, len(self._entries) - self._limit)
        removed = tuple(self._entries[:overflow])
        if overflow:
            del self._entries[:overflow]
        return removed

    def clear(self) -> None:
        self._entries.clear()

    def get(self, entry_id: str) -> TwitchChatEntry | None:
        return next(
            (entry for entry in self._entries if entry.entry_id == entry_id),
            None,
        )

    def recent_for_user(
        self, user_id: str, *, limit: int = 20
    ) -> tuple[TwitchChatEntry, ...]:
        matches = [
            entry
            for entry in self._entries
            if entry.user_id == user_id and entry.message is not None
        ]
        return tuple(matches[-max(1, limit) :])

    def mark_deleted(self, message_id: str) -> TwitchChatEntry | None:
        for index, entry in enumerate(self._entries):
            if entry.message_id == message_id:
                updated = replace(entry, deleted=True)
                self._entries[index] = updated
                return updated
        return None
