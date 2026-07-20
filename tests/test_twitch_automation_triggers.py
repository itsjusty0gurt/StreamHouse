from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.routines import RoutineStore
from twitch.automation_triggers import TwitchEventTriggerStore
from twitch.commands import TwitchCommandTriggerStore
from twitch.models import TwitchEvent, TwitchEventTransport


def twitch_event(event_type: str, event: dict) -> TwitchEvent:
    return TwitchEvent(
        subscription_type=event_type,
        version="1",
        received_at=datetime.now(timezone.utc),
        message_id="event-1",
        broadcaster_user_id="1000",
        broadcaster_user_login="sally",
        broadcaster_user_name="Sally",
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


if __name__ == "__main__":
    unittest.main()
