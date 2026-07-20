from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class TwitchFragmentType(StrEnum):
    TEXT = "text"
    CHEERMOTE = "cheermote"
    EMOTE = "emote"
    MENTION = "mention"


class TwitchEventTransport(StrEnum):
    WEBSOCKET = "websocket"
    SIMULATOR = "simulator"


@dataclass(frozen=True, slots=True)
class TwitchCheermote:
    prefix: str
    bits: int
    tier: int


@dataclass(frozen=True, slots=True)
class TwitchEmote:
    id: str
    emote_set_id: str
    owner_id: str
    formats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TwitchMention:
    user_id: str
    user_name: str
    user_login: str


@dataclass(frozen=True, slots=True)
class TwitchMessageFragment:
    type: TwitchFragmentType
    text: str
    cheermote: TwitchCheermote | None = None
    emote: TwitchEmote | None = None
    mention: TwitchMention | None = None


@dataclass(frozen=True, slots=True)
class TwitchBadge:
    set_id: str
    id: str
    info: str


@dataclass(frozen=True, slots=True)
class TwitchReply:
    parent_message_id: str
    parent_message_body: str
    parent_user_id: str
    parent_user_name: str
    parent_user_login: str
    thread_message_id: str
    thread_user_id: str
    thread_user_name: str
    thread_user_login: str


@dataclass(frozen=True, slots=True)
class TwitchMessage:
    username: str
    text: str
    received_at: datetime
    message_id: str = ""
    user_id: str = ""
    user_login: str = ""
    broadcaster_user_id: str = ""
    broadcaster_user_name: str = ""
    broadcaster_user_login: str = ""
    color: str = ""
    message_type: str = "text"
    fragments: tuple[TwitchMessageFragment, ...] = ()
    badges: tuple[TwitchBadge, ...] = ()
    bits: int | None = None
    reply: TwitchReply | None = None
    channel_points_custom_reward_id: str | None = None
    source_broadcaster_user_id: str | None = None
    source_broadcaster_user_name: str | None = None
    source_broadcaster_user_login: str | None = None
    source_message_id: str | None = None
    source_badges: tuple[TwitchBadge, ...] = ()
    is_source_only: bool = False


@dataclass(frozen=True, slots=True)
class TwitchEventDiagnostic:
    received_at: datetime
    message_id: str
    message_type: str
    subscription_type: str
    result: str
    summary: str
    status_code: int
    headers: dict[str, str]
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TwitchEvent:
    subscription_type: str
    version: str
    received_at: datetime
    message_id: str
    broadcaster_user_id: str
    broadcaster_user_login: str
    broadcaster_user_name: str
    transport: TwitchEventTransport
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TwitchChatNotice:
    kind: str
    text: str
    received_at: datetime
    target_message_id: str = ""
    target_user_login: str = ""


@dataclass(frozen=True, slots=True)
class TwitchCustomReward:
    """A broadcaster custom reward returned by Twitch Helix."""

    id: str
    title: str
    cost: int
    prompt: str = ""
    background_color: str = "#9147FF"
    is_enabled: bool = True
    is_paused: bool = False
    is_in_stock: bool = True
    is_user_input_required: bool = False
    max_per_stream_enabled: bool = False
    max_per_stream: int = 0
    max_per_user_per_stream_enabled: bool = False
    max_per_user_per_stream: int = 0
    global_cooldown_enabled: bool = False
    global_cooldown_seconds: int = 0
    should_skip_request_queue: bool = False
    manageable: bool = False

    @classmethod
    def from_dict(
        cls,
        values: dict[str, Any],
        *,
        manageable: bool = False,
    ) -> TwitchCustomReward:
        per_stream = values.get("max_per_stream_setting", {})
        per_user = values.get("max_per_user_per_stream_setting", {})
        cooldown = values.get("global_cooldown_setting", {})
        if not isinstance(per_stream, dict):
            per_stream = {}
        if not isinstance(per_user, dict):
            per_user = {}
        if not isinstance(cooldown, dict):
            cooldown = {}
        return cls(
            id=str(values.get("id", "")),
            title=str(values.get("title", "")),
            cost=max(0, int(values.get("cost", 0))),
            prompt=str(values.get("prompt", "")),
            background_color=str(values.get("background_color", "#9147FF")),
            is_enabled=bool(values.get("is_enabled", False)),
            is_paused=bool(values.get("is_paused", False)),
            is_in_stock=bool(values.get("is_in_stock", True)),
            is_user_input_required=bool(
                values.get("is_user_input_required", False)
            ),
            max_per_stream_enabled=bool(per_stream.get("is_enabled", False)),
            max_per_stream=max(0, int(per_stream.get("max_per_stream", 0))),
            max_per_user_per_stream_enabled=bool(
                per_user.get("is_enabled", False)
            ),
            max_per_user_per_stream=max(
                0, int(per_user.get("max_per_user_per_stream", 0))
            ),
            global_cooldown_enabled=bool(cooldown.get("is_enabled", False)),
            global_cooldown_seconds=max(
                0, int(cooldown.get("global_cooldown_seconds", 0))
            ),
            should_skip_request_queue=bool(
                values.get("should_redemptions_skip_request_queue", False)
            ),
            manageable=manageable,
        )
