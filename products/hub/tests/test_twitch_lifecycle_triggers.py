import json
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_providers import twitch_context_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.config.twitch import TWITCH_BOT_SCOPES, TWITCH_SCOPES
from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.automation_triggers import (
    CHANNEL_POINT_REDEMPTION_EVENT_TYPE,
    REDEMPTION_UPDATED_EVENT_TYPE,
    TwitchEventTriggerStore,
)
from products.hub.twitch.catalog import EVENTSUB_SUBSCRIPTIONS
from products.hub.twitch.live import TwitchEventSubSocket, TwitchHelixClient
from products.hub.twitch.service import TwitchConnectionState, TwitchService
from products.hub.ui.automation_page import TwitchEventTriggerDialog


DATE = "2026-11-24T10:00:00Z"
CHANNEL = {
    "broadcaster_user_id": "1",
    "broadcaster_user_login": "broadcaster",
    "broadcaster_user_name": "Broadcaster",
}
CASES = (
    ("channel.update", "2", {"title": "New title", "category_id": "", "category_name": "",
                              "language": "en", "content_classification_labels": []},
     "channel_update.title", "New title"),
    (REDEMPTION_UPDATED_EVENT_TYPE, "1",
     {"id": "redeem", "reward": {"id": "reward", "title": "Hydrate"}, "status": "fulfilled",
      "user_id": "viewer", "user_login": "viewer", "redeemed_at": DATE},
     "channel_points.status", "fulfilled"),
    ("channel.poll.begin", "1", {"choices": [{"id": "choice", "title": "Yes"}]}, "poll.choices", "Yes"),
    ("channel.poll.progress", "1", {"choices": [{"title": "Yes", "votes": 3}]}, "poll.choices", "Yes (3 votes)"),
    ("channel.poll.end", "1", {"status": "completed", "ended_at": DATE}, "poll.status", "completed"),
    ("channel.prediction.begin", "1", {"outcomes": [{"id": "win", "title": "Wins"}]},
     "prediction.outcome_ids", "win"),
    ("channel.prediction.progress", "1", {"outcomes": [{"title": "Wins", "users": 3, "channel_points": 90}]},
     "prediction.outcomes", "Wins (3 users, 90 points)"),
    ("channel.prediction.lock", "1", {"locked_at": DATE}, "prediction.locked_at", DATE),
    ("channel.prediction.end", "1", {"winning_outcome_id": "win", "outcomes": [{"id": "win", "title": "Wins"}]},
     "prediction.winning_outcome", "Wins"),
    ("channel.hype_train.begin", "2", {"level": 1, "progress": 0, "goal": 500}, "hype_train.progress", "0"),
    ("channel.hype_train.progress", "2", {"top_contributions": [{"user_name": "Viewer", "type": "bits", "total": 12}]},
     "hype_train.top_contributions", "Viewer (12 total, bits)"),
    ("channel.hype_train.end", "2", {"total": 1500, "ended_at": DATE}, "hype_train.total", "1500"),
    ("channel.shield_mode.begin", "1", {"started_at": DATE, "moderator_user_login": "mod"},
     "moderator.login", "mod"),
    ("channel.shield_mode.end", "1", {"ended_at": DATE}, "shield_mode.ended_at", DATE),
    ("channel.shoutout.create", "1", {"to_broadcaster_user_id": "2"}, "shoutout.other_id", "2"),
    ("channel.shoutout.receive", "1", {"from_broadcaster_user_login": "other"}, "shoutout.other_login", "other"),
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def frame(event_type, version, values, message_id="event-1"):
    return json.dumps({
        "metadata": {"message_type": "notification", "message_id": message_id,
                     "message_timestamp": DATE, "subscription_type": event_type,
                     "subscription_version": version},
        "payload": {"subscription": {"type": event_type, "version": version},
                    "event": {**CHANNEL, **values}},
    })


@pytest.mark.parametrize("event_type,version,values,name,expected", CASES)
def test_lifecycle_websocket_dispatch_registry_and_reconnect(
    app, tmp_path, event_type, version, values, name, expected,
):
    routines = RoutineStore(tmp_path / "routines.json")
    routine = routines.add("Twitch lifecycle")
    path = tmp_path / "event_triggers.json"
    store = TwitchEventTriggerStore(path, routines)
    trigger = store.add(routine.routine_id, event_type)
    store.add(routine.routine_id, event_type, enabled=False)
    restored = TwitchEventTriggerStore(path, routines)
    restored.load()
    assert restored.get(trigger.trigger_id).event_type == event_type
    registry = VariableRegistry()
    registry.register(twitch_context_provider())
    dispatched, welcomes = [], []
    socket = TwitchEventSubSocket(
        on_welcome=welcomes.append, on_message=Mock(), on_notification=Mock(),
        on_diagnostic=Mock(), on_revocation=Mock(), on_error=Mock(),
        on_bus_event=lambda event: dispatched.extend(restored.evaluate(event)),
    )
    notification = frame(event_type, version, values)
    socket._receive_text(notification)
    socket._reconnect_transfer = True  # Native transfer retains this socket's ingress history.
    socket._receive_text(json.dumps({
        "metadata": {"message_type": "session_welcome", "message_id": "transfer"},
        "payload": {"session": {"id": "new-session"}},
    }))
    socket._receive_text(notification)
    assert welcomes == []
    assert [result.trigger_id for result in dispatched] == [trigger.trigger_id]
    context = dispatched[0].context
    assert context[name] == expected
    assert context["event.broadcaster_id"] == "1"
    assert registry.resolve(name, context).available
    assert not registry.resolve(name).available
    if event_type == "channel.update":
        assert registry.resolve("channel_update.category_id", context).value == ""
    socket._receive_text(frame(event_type, version, {}, message_id="event-2"))
    assert len(dispatched) == 2  # Missing optional fields do not suppress a new notification.
    socket.close()


def make_token(scopes):
    token = Mock(spec=TwitchToken)
    token.scopes = scopes
    token.user_id = "1"
    return token


def test_lifecycle_subscription_versions_permissions_and_conditions():
    client = TwitchHelixClient()
    client._create_subscription = Mock()
    token = make_token(TWITCH_SCOPES)
    assert client.create_automation_activity_subscriptions("session", "1", token, lambda: False) == ()
    calls = client._create_subscription.call_args_list
    expected = {(event_type, version) for event_type, version, *_ in CASES}
    assert len(calls) == len(expected)
    assert {(call.args[0], call.args[1]) for call in calls} == expected
    assert expected <= {(subscription.type, subscription.version) for subscription in EVENTSUB_SUBSCRIPTIONS}
    for call in calls:
        event_type, _, condition, session, used_token = call.args
        expected_condition = {"broadcaster_user_id": "1"}
        if event_type.startswith(("channel.shield_mode.", "channel.shoutout.")):
            expected_condition["moderator_user_id"] = "1"
        assert condition == expected_condition
        assert session == "session" and used_token is token
    client._create_subscription.reset_mock()
    warnings = client.create_automation_activity_subscriptions(
        "session", "1", make_token(TWITCH_BOT_SCOPES), lambda: False,
    )
    assert [call.args[:2] for call in client._create_subscription.call_args_list] == [("channel.update", "2")]
    assert len(warnings) == 6
    client._create_subscription.reset_mock()
    client.create_automation_activity_subscriptions(
        "session", "1", make_token(("channel:read:redemptions", "channel:manage:polls",
                                    "channel:manage:predictions", "moderator:manage:shield_mode",
                                    "moderator:read:shoutouts")), lambda: False,
    )
    assert client._create_subscription.call_count == len(expected) - 3  # No Hype Train scope.
    client._create_subscription.reset_mock()
    assert client.create_automation_activity_subscriptions("session", "1", token, lambda: True) == ()
    client._create_subscription.assert_not_called()


def test_subscription_job_does_not_duplicate_or_survive_a_fresh_session(monkeypatch):
    jobs = []
    monkeypatch.setattr("products.hub.twitch.service.Thread",
                        lambda **args: Mock(start=lambda: jobs.append(args["target"])))
    helix = Mock()
    helix.create_automation_activity_subscriptions.return_value = ()
    service = TwitchService(helix=helix)
    service.state = TwitchConnectionState.CONNECTED
    service.broadcaster_user_id = "1"
    token = make_token(TWITCH_SCOPES)
    service._start_automation_activity_subscriptions("first", token)
    first = service._automation_subscription_cancel
    service._start_automation_activity_subscriptions("first", token)
    service._start_automation_activity_subscriptions("next", token)
    assert first.is_set() and len(jobs) == 2
    jobs[1]()
    assert helix.create_automation_activity_subscriptions.call_args.args[:3] == ("next", "1", token)
    current = service._automation_subscription_cancel
    service.disconnect()
    assert current.is_set()


def test_redemption_added_and_status_updates_match_their_own_triggers(app, tmp_path):
    routines = RoutineStore(tmp_path / "routines.json")
    routine = routines.add("Reward changes")
    store = TwitchEventTriggerStore(tmp_path / "triggers.json", routines)
    added = store.add_channel_point_redemption(routine.routine_id, reward_id="reward")
    updated = store.add(routine.routine_id, REDEMPTION_UPDATED_EVENT_TYPE,
                        reward_id="reward", filters={"status": "fulfilled"})
    results = []
    socket = TwitchEventSubSocket(
        on_welcome=Mock(), on_message=Mock(), on_notification=Mock(), on_diagnostic=Mock(),
        on_revocation=Mock(), on_error=Mock(),
        on_bus_event=lambda event: results.extend(store.evaluate(event)),
    )
    for i, (event_type, reward, status) in enumerate((
        (CHANNEL_POINT_REDEMPTION_EVENT_TYPE, "reward", "fulfilled"),
        (REDEMPTION_UPDATED_EVENT_TYPE, "reward", "canceled"),
        (REDEMPTION_UPDATED_EVENT_TYPE, "other", "fulfilled"),
        (REDEMPTION_UPDATED_EVENT_TYPE, "reward", "FULFILLED"),
    )):
        socket._receive_text(frame(event_type, "1", {"id": "same-redemption", "reward": {"id": reward},
                                                     "status": status}, str(i)))
    assert [result.trigger_id for result in results] == [added.trigger_id, updated.trigger_id]
    socket.close()


def test_lifecycle_triggers_are_listed_in_editor(app):
    dialog = TwitchEventTriggerDialog()
    assert all(dialog.event_type_combo.findData(event_type) >= 0 for event_type, *_ in CASES)
    dialog.close()
