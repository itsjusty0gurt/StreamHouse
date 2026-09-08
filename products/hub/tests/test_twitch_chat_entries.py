from datetime import datetime, timezone

from products.hub.twitch.chat_entries import (
    TwitchChatEntry,
    TwitchChatEntryType,
    TwitchChatHistory,
)
from products.hub.twitch.models import TwitchChatNotice, TwitchMessage


def message(number: int, *, user_id: str = "viewer-1") -> TwitchMessage:
    return TwitchMessage(
        username="Viewer",
        text=f"message {number}",
        received_at=datetime.now(timezone.utc),
        message_id=f"message-{number}",
        user_id=user_id,
        broadcaster_user_id="channel-1",
        message_type="text",
    )


def test_history_is_bounded_without_flattening_message_metadata() -> None:
    history = TwitchChatHistory(limit=2)
    history.add(TwitchChatEntry.from_message(message(1)))
    history.add(TwitchChatEntry.from_message(message(2)))
    removed = history.add(TwitchChatEntry.from_message(message(3)))

    assert [entry.message_id for entry in removed] == ["message-1"]
    assert [entry.message_id for entry in history.entries] == [
        "message-2",
        "message-3",
    ]
    assert history.entries[-1].message.broadcaster_user_id == "channel-1"


def test_recent_user_messages_and_deletion_are_entry_scoped() -> None:
    history = TwitchChatHistory()
    history.add(TwitchChatEntry.from_message(message(1)))
    history.add(TwitchChatEntry.from_message(message(2, user_id="viewer-2")))
    history.add(TwitchChatEntry.from_message(message(3)))

    assert [entry.message_id for entry in history.recent_for_user("viewer-1")] == [
        "message-1",
        "message-3",
    ]
    assert history.mark_deleted("message-1").deleted is True
    assert history.get("message-message-2").deleted is False


def test_message_removal_uses_stable_message_id_and_is_idempotent() -> None:
    history = TwitchChatHistory()
    history.add(TwitchChatEntry.from_message(message(1)))
    history.add(TwitchChatEntry.from_message(message(2)))

    removed = history.remove_message("message-1")

    assert removed is not None
    assert removed.message_id == "message-1"
    assert [entry.message_id for entry in history.entries] == ["message-2"]
    assert history.remove_message("unknown") is None


def test_user_message_removal_uses_stable_user_id_not_display_name() -> None:
    history = TwitchChatHistory()
    history.add(TwitchChatEntry.from_message(message(1, user_id="viewer-1")))
    same_name = message(2, user_id="viewer-2")
    history.add(TwitchChatEntry.from_message(same_name))
    history.add(TwitchChatEntry.from_message(message(3, user_id="viewer-1")))

    removed = history.remove_user_messages("viewer-1")

    assert [entry.message_id for entry in removed] == ["message-1", "message-3"]
    assert [entry.message_id for entry in history.entries] == ["message-2"]
    assert history.remove_user_messages("unknown") == ()


def test_notice_types_allow_special_rendering_without_changing_messages() -> None:
    moderation = TwitchChatEntry.from_notice(
        TwitchChatNotice(
            kind="timeout",
            text="Viewer was timed out",
            received_at=datetime.now(timezone.utc),
        )
    )
    event = TwitchChatEntry.from_notice(
        TwitchChatNotice(
            kind="notice",
            text="A raid arrived",
            received_at=datetime.now(timezone.utc),
        )
    )

    assert moderation.kind is TwitchChatEntryType.MODERATION
    assert event.kind is TwitchChatEntryType.TWITCH_EVENT
