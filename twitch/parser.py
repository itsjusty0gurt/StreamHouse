from __future__ import annotations

from datetime import datetime
from typing import Any

from twitch.models import (
    TwitchBadge,
    TwitchCheermote,
    TwitchEmote,
    TwitchFragmentType,
    TwitchMention,
    TwitchMessage,
    TwitchMessageFragment,
    TwitchReply,
)


class TwitchPayloadError(ValueError):
    """Raised when an EventSub payload cannot be converted safely."""


class TwitchMessageParser:
    """Convert Twitch channel.chat.message event data into Sally models."""

    @classmethod
    def parse(
        cls,
        event: dict[str, Any],
        received_at: datetime,
    ) -> TwitchMessage:
        try:
            message = event["message"]
            text = str(message["text"])
            username = str(event["chatter_user_name"])
        except (KeyError, TypeError) as error:
            raise TwitchPayloadError(
                "Chat notification is missing required message fields."
            ) from error

        if not isinstance(message, dict):
            raise TwitchPayloadError("The message field must be an object.")

        fragments = tuple(
            cls._parse_fragment(fragment)
            for fragment in cls._object_list(message.get("fragments"))
        )
        badges = cls._parse_badges(event.get("badges"))
        source_badges = cls._parse_badges(event.get("source_badges"))
        cheer = event.get("cheer")
        bits = int(cheer["bits"]) if isinstance(cheer, dict) else None

        return TwitchMessage(
            username=username,
            text=text,
            received_at=received_at,
            message_id=str(event.get("message_id", "")),
            user_id=str(event.get("chatter_user_id", "")),
            user_login=str(event.get("chatter_user_login", "")),
            broadcaster_user_id=str(event.get("broadcaster_user_id", "")),
            broadcaster_user_name=str(
                event.get("broadcaster_user_name", "")
            ),
            broadcaster_user_login=str(
                event.get("broadcaster_user_login", "")
            ),
            color=str(event.get("color", "")),
            message_type=str(event.get("message_type", "text")),
            fragments=fragments,
            badges=badges,
            bits=bits,
            reply=cls._parse_reply(event.get("reply")),
            channel_points_custom_reward_id=(
                event.get("channel_points_custom_reward_id")
            ),
            source_broadcaster_user_id=event.get(
                "source_broadcaster_user_id"
            ),
            source_broadcaster_user_name=event.get(
                "source_broadcaster_user_name"
            ),
            source_broadcaster_user_login=event.get(
                "source_broadcaster_user_login"
            ),
            source_message_id=event.get("source_message_id"),
            source_badges=source_badges,
            is_source_only=bool(event.get("is_source_only", False)),
        )

    @classmethod
    def _parse_fragment(
        cls,
        fragment: dict[str, Any],
    ) -> TwitchMessageFragment:
        try:
            fragment_type = TwitchFragmentType(str(fragment["type"]))
            text = str(fragment["text"])
        except (KeyError, ValueError) as error:
            raise TwitchPayloadError("Invalid chat message fragment.") from error

        cheermote_data = fragment.get("cheermote")
        emote_data = fragment.get("emote")
        mention_data = fragment.get("mention")

        cheermote = None
        if isinstance(cheermote_data, dict):
            cheermote = TwitchCheermote(
                prefix=str(cheermote_data.get("prefix", "")),
                bits=int(cheermote_data.get("bits", 0)),
                tier=int(cheermote_data.get("tier", 0)),
            )

        emote = None
        if isinstance(emote_data, dict):
            formats = emote_data.get("format", ())
            emote = TwitchEmote(
                id=str(emote_data.get("id", "")),
                emote_set_id=str(emote_data.get("emote_set_id", "")),
                owner_id=str(emote_data.get("owner_id", "")),
                formats=tuple(str(value) for value in formats),
            )

        mention = None
        if isinstance(mention_data, dict):
            mention = TwitchMention(
                user_id=str(mention_data.get("user_id", "")),
                user_name=str(mention_data.get("user_name", "")),
                user_login=str(mention_data.get("user_login", "")),
            )

        return TwitchMessageFragment(
            type=fragment_type,
            text=text,
            cheermote=cheermote,
            emote=emote,
            mention=mention,
        )

    @staticmethod
    def _parse_badges(value: Any) -> tuple[TwitchBadge, ...]:
        return tuple(
            TwitchBadge(
                set_id=str(badge.get("set_id", "")),
                id=str(badge.get("id", "")),
                info=str(badge.get("info", "")),
            )
            for badge in TwitchMessageParser._object_list(value)
        )

    @staticmethod
    def _parse_reply(value: Any) -> TwitchReply | None:
        if not isinstance(value, dict):
            return None

        return TwitchReply(
            parent_message_id=str(value.get("parent_message_id", "")),
            parent_message_body=str(value.get("parent_message_body", "")),
            parent_user_id=str(value.get("parent_user_id", "")),
            parent_user_name=str(value.get("parent_user_name", "")),
            parent_user_login=str(value.get("parent_user_login", "")),
            thread_message_id=str(value.get("thread_message_id", "")),
            thread_user_id=str(value.get("thread_user_id", "")),
            thread_user_name=str(value.get("thread_user_name", "")),
            thread_user_login=str(value.get("thread_user_login", "")),
        )

    @staticmethod
    def _object_list(value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise TwitchPayloadError("Expected a list of objects.")
        return value
