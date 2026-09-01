from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from products.hub.automation.routines import RoutineStore
from products.hub.twitch.automation_triggers import (
    CHANNEL_POINT_REDEMPTION_EVENT_TYPE,
    TwitchEventTriggerStore,
)
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.twitch.models import TwitchEvent, TwitchEventTransport, TwitchMessage


def twitch_event(event_type: str, event: dict) -> TwitchEvent:
    return TwitchEvent(
        subscription_type=event_type,
        version="1",
        received_at=datetime.now(timezone.utc),
        message_id="event-1",
        broadcaster_user_id="1000",
        broadcaster_user_login="streamer",
        broadcaster_user_name="Streamer",
        transport=TwitchEventTransport.SIMULATOR,
        payload={"event": event},
    )


class TwitchEventTriggerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.routines = RoutineStore(root / "routines.json")
        self.store = TwitchEventTriggerStore(
            root / "event_triggers.json", self.routines
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_event_trigger_round_trip_links_to_routine(self) -> None:
        routine = self.routines.add("Welcome followers")
        trigger = self.store.add(routine.routine_id, "channel.follow")

        loaded_routines = RoutineStore(self.routines.path)
        loaded = TwitchEventTriggerStore(self.store.path, loaded_routines)
        saved = loaded.load()[0]

        self.assertEqual(saved.trigger_id, trigger.trigger_id)
        self.assertEqual(saved.event_type, "channel.follow")
        self.assertIn(
            saved.trigger_id,
            loaded_routines.get(routine.routine_id).trigger_ids,
        )

    def test_filters_match_nested_event_fields_case_insensitively(self) -> None:
        routine = self.routines.add("Hydrate")
        self.store.add(
            routine.routine_id,
            "channel.channel_points_custom_reward_redemption.add",
            filters={"reward.title": "Hydrate"},
        )
        matching = twitch_event(
            "channel.channel_points_custom_reward_redemption.add",
            {
                "user_name": "Viewer",
                "user_input": "big sip",
                "reward": {"id": "reward-1", "title": "hydrate", "cost": 500},
            },
        )
        other = twitch_event(
            "channel.channel_points_custom_reward_redemption.add",
            {"reward": {"title": "Stretch"}},
        )

        results = self.store.evaluate(matching)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].context["user"], "Viewer")
        self.assertEqual(results[0].context["input"], "big sip")
        self.assertEqual(results[0].context["reward"], "hydrate")
        self.assertEqual(results[0].context["reward_cost"], "500")
        self.assertEqual(self.store.evaluate(other), ())

    def test_redemption_matches_stable_reward_id_and_exposes_full_context(self) -> None:
        hydrate = self.routines.add("Hydrate")
        any_reward = self.routines.add("Any reward")
        specific = self.store.add_channel_point_redemption(
            hydrate.routine_id, reward_id="reward-1", reward_title="Old title"
        )
        wildcard = self.store.add_channel_point_redemption(any_reward.routine_id)
        event = twitch_event(
            CHANNEL_POINT_REDEMPTION_EVENT_TYPE,
            {
                "id": "redemption-1",
                "user_id": "viewer-1",
                "user_login": "viewer",
                "user_name": "Viewer",
                "user_input": "",
                "status": "unfulfilled",
                "redeemed_at": "2026-08-31T12:00:00Z",
                "reward": {
                    "id": "reward-1",
                    "title": "Renamed reward",
                    "cost": 500,
                    "prompt": "Drink water",
                },
            },
        )

        results = self.store.evaluate(event)

        self.assertEqual({item.trigger_id for item in results}, {specific.trigger_id, wildcard.trigger_id})
        context = results[0].context
        self.assertEqual(context["user_id"], "viewer-1")
        self.assertEqual(context["user_login"], "viewer")
        self.assertEqual(context["channel_points.redemption_id"], "redemption-1")
        self.assertEqual(context["channel_points.reward_id"], "reward-1")
        self.assertEqual(context["channel_points.reward_title"], "Renamed reward")
        self.assertEqual(context["channel_points.reward_cost"], "500")
        self.assertEqual(context["channel_points.reward_prompt"], "Drink water")
        self.assertEqual(context["channel_points.user_input"], "")
        self.assertEqual(context["channel_points.status"], "unfulfilled")
        self.assertEqual(context["channel_points.redeemed_at"], "2026-08-31T12:00:00Z")

        other = twitch_event(
            CHANNEL_POINT_REDEMPTION_EVENT_TYPE,
            {"reward": {"id": "reward-2", "title": "Other", "cost": 1}},
        )
        self.assertEqual(
            [item.trigger_id for item in self.store.evaluate(other)],
            [wildcard.trigger_id],
        )

    def test_redemption_reward_identity_round_trips(self) -> None:
        routine = self.routines.add("Hydrate")
        trigger = self.store.add_channel_point_redemption(
            routine.routine_id, reward_id="reward-1", reward_title="Hydrate"
        )
        self.assertEqual(json.loads(self.store.path.read_text(encoding="utf-8"))["version"], 3)

        loaded = TwitchEventTriggerStore(self.store.path, self.routines)
        saved = loaded.load()[0]

        self.assertEqual(saved.trigger_id, trigger.trigger_id)
        self.assertEqual(saved.reward_id, "reward-1")
        self.assertEqual(saved.reward_title, "Hydrate")

    def test_routine_can_combine_command_and_multiple_event_triggers(self) -> None:
        commands = TwitchCommandTriggerStore(
            Path(self.temporary.name) / "commands.json", self.routines
        )
        command = commands.add("hello", "Hello")
        follow = self.store.add(command.routine_id, "channel.follow")
        raid = self.store.add(command.routine_id, "channel.raid")

        routine = self.routines.get(command.routine_id)

        self.assertEqual(
            routine.trigger_ids,
            (command.trigger_id, follow.trigger_id, raid.trigger_id),
        )
        self.assertEqual(self.routines.matching(follow.trigger_id)[0], routine)
        self.assertTrue(self.store.delete(follow.trigger_id))
        self.assertEqual(
            self.routines.get(command.routine_id).trigger_ids,
            (command.trigger_id, raid.trigger_id),
        )

    def test_unsupported_live_event_type_is_rejected(self) -> None:
        routine = self.routines.add("Unsupported")
        with self.assertRaisesRegex(ValueError, "not connected"):
            self.store.add(routine.routine_id, "channel.poll.end")

    def test_first_message_fires_once_per_viewer_during_stream(self) -> None:
        routine = self.routines.add("Welcome viewers")
        trigger = self.store.add(
            routine.routine_id,
            "channel.chat.first_message",
        )
        started = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        self.store.observe_stream({"id": "stream-1"}, started)
        message = TwitchMessage(
            username="Viewer",
            user_id="viewer-1",
            text="hello",
            received_at=started + timedelta(minutes=1),
            broadcaster_user_name="Streamer",
        )

        first = self.store.evaluate_first_message(
            message,
            stream_is_live=True,
        )
        repeated = self.store.evaluate_first_message(
            message,
            stream_is_live=True,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].trigger_id, trigger.trigger_id)
        self.assertEqual(first[0].context["user"], "Viewer")
        self.assertEqual(first[0].context["message"], "hello")
        self.assertEqual(repeated, ())

    def test_short_offline_period_does_not_repeat_welcome(self) -> None:
        routine = self.routines.add("Welcome viewers")
        self.store.add(
            routine.routine_id,
            "channel.chat.first_message",
            reset_minutes=15,
        )
        started = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        message = TwitchMessage(
            username="Viewer",
            user_id="viewer-1",
            text="hello",
            received_at=started,
        )
        self.store.observe_stream({"id": "stream-1"}, started)
        self.assertEqual(
            len(self.store.evaluate_first_message(message, stream_is_live=True)),
            1,
        )
        self.store.observe_stream(None, started + timedelta(minutes=5))
        self.store.observe_stream(
            {"id": "stream-1"},
            started + timedelta(minutes=10),
        )

        self.assertEqual(
            self.store.evaluate_first_message(message, stream_is_live=True),
            (),
        )

    def test_welcomes_reset_after_offline_grace_period(self) -> None:
        routine = self.routines.add("Welcome viewers")
        trigger = self.store.add(
            routine.routine_id,
            "channel.chat.first_message",
            reset_minutes=15,
        )
        started = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
        message = TwitchMessage(
            username="Viewer",
            user_id="viewer-1",
            text="hello",
            received_at=started,
        )
        self.store.observe_stream({"id": "stream-1"}, started)
        self.store.evaluate_first_message(message, stream_is_live=True)
        self.store.observe_stream(None, started + timedelta(minutes=1))
        self.store.observe_stream(
            {"id": "stream-2"},
            started + timedelta(minutes=17),
        )

        result = self.store.evaluate_first_message(
            message,
            stream_is_live=True,
            observed_at=started + timedelta(minutes=18),
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].trigger_id, trigger.trigger_id)

    def test_keyword_phrase_contains_context_and_normal_chat_identity(self) -> None:
        routine = self.routines.add("Coffee response")
        trigger = self.store.add_keyword_phrase(routine.routine_id, "coffee")
        message = TwitchMessage(
            username="Viewer",
            user_id="viewer-1",
            user_login="viewer",
            text="I think coffee is better than tea",
            message_id="message-1",
            received_at=datetime.now(timezone.utc),
        )

        result = self.store.evaluate_keyword_phrase(message)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].trigger_id, trigger.trigger_id)
        self.assertEqual(result[0].context["keyword.message"], message.text)
        self.assertEqual(result[0].context["keyword.match"], "coffee")
        self.assertEqual(result[0].context["keyword.before"], "I think")
        self.assertEqual(result[0].context["keyword.after"], "is better than tea")
        self.assertEqual(result[0].context["user_id"], "viewer-1")
        self.assertEqual(result[0].context["message_id"], "message-1")
        self.assertNotIn("command_data", result[0].context)

    def test_keyword_phrase_match_modes_case_and_whole_word(self) -> None:
        cases = (
            ("contains", "Coffee time", "coffee", True),
            ("exact", "COFFEE", "coffee", True),
            ("starts_with", "coffee time", "coffee", True),
            ("ends_with", "more coffee", "coffee", True),
            ("contains", "category", "cat", False),
        )
        for index, (match_type, text, phrase, expected) in enumerate(cases):
            routine = self.routines.add(f"Keyword {index}")
            self.store.add_keyword_phrase(
                routine.routine_id,
                phrase,
                match_type=match_type,
                ignore_case=True,
                whole_word=True,
            )
            message = TwitchMessage(
                username="Viewer",
                user_id=f"viewer-{index}",
                text=text,
                received_at=datetime.now(timezone.utc),
            )
            matches = tuple(
                item
                for item in self.store.evaluate_keyword_phrase(message)
                if item.trigger_id in self.routines.get(routine.routine_id).trigger_ids
            )
            self.assertEqual(bool(matches), expected, (match_type, text, phrase))

    def test_keyword_phrase_preserves_canonical_phrase_and_empty_edges(self) -> None:
        routine = self.routines.add("Phrase response")
        self.store.add_keyword_phrase(
            routine.routine_id,
            "I love you Sally",
            ignore_case=True,
        )
        leading = TwitchMessage(
            username="Viewer",
            user_id="one",
            text="I LOVE YOU SALLY lol",
            received_at=datetime.now(timezone.utc),
        )
        trailing = TwitchMessage(
            username="Viewer",
            user_id="two",
            text="Hey, I love you Sally",
            received_at=datetime.now(timezone.utc),
        )

        first = self.store.evaluate_keyword_phrase(leading)[0].context
        second = self.store.evaluate_keyword_phrase(trailing)[0].context

        self.assertEqual(first["keyword.match"], "I love you Sally")
        self.assertEqual(first["keyword.before"], "")
        self.assertEqual(first["keyword.after"], "lol")
        self.assertEqual(second["keyword.before"], "Hey,")
        self.assertEqual(second["keyword.after"], "")

    def test_ads_warning_started_and_ended_use_normal_routine_links(self) -> None:
        expected = (
            "ads.warning.5_minutes",
            "ads.warning.3_minutes",
            "ads.warning.2_minutes",
            "ads.warning.1_minute",
            "ads.started",
            "ads.ended",
        )
        for event_type in expected:
            routine = self.routines.add(event_type)
            trigger = self.store.add(routine.routine_id, event_type)

            events = self.store.evaluate_named(
                event_type,
                {"ads.next_in": "300", "ads.in_progress": "false"},
                trigger_type="ads",
            )

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].trigger_id, trigger.trigger_id)
            self.assertEqual(events[0].trigger_type, "ads")
            self.assertEqual(events[0].context["ads.next_in"], "300")

    def test_keyword_and_ads_current_formats_round_trip(self) -> None:
        keyword_routine = self.routines.add("Keyword")
        keyword = self.store.add_keyword_phrase(
            keyword_routine.routine_id,
            "coffee",
            match_type="starts_with",
            ignore_case=False,
            whole_word=True,
        )
        ads_routine = self.routines.add("Ads Started")
        ads = self.store.add(ads_routine.routine_id, "ads.started")

        loaded = TwitchEventTriggerStore(self.store.path, self.routines)
        loaded.load()

        self.assertEqual(loaded.get(keyword.trigger_id).filters["phrase"], "coffee")
        self.assertEqual(
            loaded.get(keyword.trigger_id).filters["match_type"], "starts_with"
        )
        self.assertEqual(loaded.get(ads.trigger_id).event_type, "ads.started")

    def test_obsolete_or_unversioned_schema_is_rejected(self) -> None:
        for payload in (
            {"triggers": []},
            {"version": 1, "triggers": []},
            {"version": 2, "triggers": []},
            {"version": "2", "triggers": []},
            {"version": 4, "triggers": []},
        ):
            with self.subTest(payload=payload):
                self.store.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsupported Twitch event"):
                    self.store.load()

    def test_current_schema_does_not_invent_missing_trigger_ids(self) -> None:
        routine = self.routines.add("Follow")
        self.store.path.write_text(
            json.dumps(
                {
                    "version": self.store.VERSION,
                    "triggers": [
                        {
                            "routine_id": routine.routine_id,
                            "event_type": "channel.follow",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(self.store.load(), [])


if __name__ == "__main__":
    unittest.main()
