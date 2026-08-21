from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from products.hub.automation.routines import RoutineStore
from products.hub.automation.simulator import TwitchCommandSimulator
from products.hub.twitch.commands import (
    TwitchCommandPermission,
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerStore,
)


class TwitchCommandSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.routines = RoutineStore(root / "automation" / "routines.json")
        self.commands = TwitchCommandTriggerStore(
            root / "twitch" / "commands.json",
            self.routines,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_simulates_rendered_chat_without_recording_command_use(self) -> None:
        command = self.commands.add(
            "status",
            "Mic is {obs.muted}; playing {stream.category}; hi {user.display_name}.",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        simulator = TwitchCommandSimulator(
            self.commands,
            self.routines,
            live_context={"stream.category": "Science & Technology", "obs.muted": "false"},
        )

        result = simulator.simulate("!status", username="Yogurt")

        self.assertEqual(result.outcome, TwitchCommandTriggerOutcome.READY.value)
        self.assertEqual(result.routine_name, "Command !status")
        self.assertEqual(command.uses, 0)
        self.assertEqual(len(result.sent_messages), 1)
        self.assertEqual(
            result.sent_messages[0].message,
            "Mic is false; playing Science & Technology; hi Yogurt.",
        )
        self.assertTrue(result.sent_messages[0].as_bot)
        self.assertFalse(result.missing_variables)

    def test_blank_response_command_can_simulate_other_tasks(self) -> None:
        command = self.commands.add(
            "open",
            "",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.routines.add_task(
            command.routine_id,
            task_type="core.open_target",
            name="Open Twitch",
            config={"target": "https://twitch.tv/{stream.channel}"},
        )
        simulator = TwitchCommandSimulator(
            self.commands,
            self.routines,
            live_context={"stream.channel": "itsjusty0gurt"},
        )

        result = simulator.simulate("!open")

        self.assertEqual(result.outcome, TwitchCommandTriggerOutcome.READY.value)
        self.assertFalse(result.sent_messages)
        self.assertEqual(len(result.task_results), 1)
        self.assertIn(
            "target=https://twitch.tv/itsjusty0gurt",
            result.task_results[0].detail,
        )

    def test_reports_permission_denial(self) -> None:
        self.commands.add(
            "mods",
            "hi",
            permission=TwitchCommandPermission.MODERATOR.value,
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        simulator = TwitchCommandSimulator(self.commands, self.routines)

        result = simulator.simulate("!mods")

        self.assertEqual(result.outcome, TwitchCommandTriggerOutcome.DENIED.value)
        self.assertEqual(result.invocation, "mods")

    def test_reports_missing_variables(self) -> None:
        self.commands.add(
            "status",
            "Playing {stream.category} with mic {obs.muted}.",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        simulator = TwitchCommandSimulator(self.commands, self.routines)

        result = simulator.simulate("!status")

        self.assertEqual(result.outcome, TwitchCommandTriggerOutcome.READY.value)
        self.assertEqual(result.missing_variables, ("obs.muted", "stream.category"))
        self.assertFalse(result.sent_messages)
        self.assertFalse(result.task_results[0].succeeded)


if __name__ == "__main__":
    unittest.main()
