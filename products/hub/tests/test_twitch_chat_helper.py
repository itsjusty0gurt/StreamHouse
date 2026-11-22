from dataclasses import replace
from datetime import datetime, timezone

import pytest

from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.chat_helper import (
    TwitchUserSuggestion, matching_chat_users, mentions_authenticated_broadcaster,
    mentions_broadcaster,
)
from products.hub.twitch.models import TwitchMessage


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@ExampleStreamer hello", True),
        ("Hey @EXAMPLESTREAMER! @someone", True),
        ("@Someone @example_alt, thanks", True),
        ("@example_alt @ExampleStreamer.", True),
        ("@ExampleStreamer123", False),
        ("@ExampleStreamer_", False),
        ("ExampleStreamer", False),
        ("user@ExampleStreamer", False),
        ("https://example.test/@ExampleStreamer", False),
        ("@@ExampleStreamer", False),
    ],
)
def test_broadcaster_mention_uses_complete_tokens(text, expected):
    assert mentions_broadcaster(text, "example_alt", "ExampleStreamer") is expected


def test_mentions_use_only_authenticated_identity_for_the_destination():
    token = TwitchToken("access", "refresh", 999, [], "owner", "login")
    message = TwitchMessage(
        "Viewer", "@DisplayName", datetime.now(timezone.utc),
        broadcaster_user_id="owner", broadcaster_user_name="DisplayName",
    )
    assert mentions_authenticated_broadcaster(message, token, "", "")
    assert not mentions_authenticated_broadcaster(message, None, "owner", "DisplayName")
    assert not mentions_authenticated_broadcaster(
        replace(message, broadcaster_user_id="different"), token, "owner", "DisplayName"
    )
    no_metadata = replace(message, broadcaster_user_id="", broadcaster_user_name="")
    assert mentions_authenticated_broadcaster(no_metadata, token, "owner", "DisplayName")
    assert not mentions_authenticated_broadcaster(no_metadata, token, "different", "DisplayName")


def test_local_username_suggestions_deduplicate_and_rank_without_a_second_cache():
    users = (
        TwitchUserSuggestion("old", "Example One", "1", "2026-01-01"),
        TwitchUserSuggestion("recent", "Example Two", "2", "2026-11-21"),
        TwitchUserSuggestion("RECENT", "Example Duplicate", "3", "2026-11-21"),
        TwitchUserSuggestion("different", "Example Same ID", "2", "2026-11-21"),
        TwitchUserSuggestion("exlogin", "", "5", "2026-01-01"),
        TwitchUserSuggestion("not a login", "Example Invalid", "4"),
    )
    assert [result.login for result in matching_chat_users(users, "ex", limit=3)] == [
        "exlogin", "recent", "old"
    ]
    assert matching_chat_users(iter(()), "ex") == ()
