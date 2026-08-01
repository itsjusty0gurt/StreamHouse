from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from products.hub.automation.routines import RoutineStore
from products.hub.twitch.commands import (
    TwitchCommandPermission,
    TwitchCommandTriggerDispatcher,
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerStore,
)
from products.hub.twitch.models import TwitchBadge, TwitchMessage
from products.hub.twitch.tasks import SendTwitchChatMessageTask


def message(
    text: str,
    *,
    user_id: str = "viewer-1",
    badges: tuple[TwitchBadge, ...] = (),
) -> TwitchMessage:
    return TwitchMessage(
        username="TestViewer",
        text=text,
        received_at=datetime.now(timezone.utc),
        user_id=user_id,
        user_login="testviewer",
        broadcaster_user_id="broadcaster-1",
        badges=badges,
    )


class TwitchCommandTriggerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.path = root / "commands.json"
        self.routine_store = RoutineStore(root / "routines.json")
        self.store = TwitchCommandTriggerStore(self.path, self.routine_store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_trigger_crud_round_trip_manages_its_routine_and_task(self) -> None:
        trigger = self.store.add(
            "Discord",
            "Join us, {user}!",
            aliases=["dc"],
            permission=TwitchCommandPermission.SUBSCRIBER.value,
            global_cooldown_seconds=5,
            user_cooldown_seconds=15,
        )
        self.store.record_use(trigger.trigger_id)
        self.assertIs(self.store.resolve("!DC"), trigger)
        routine = self.routine_store.get(trigger.routine_id)
        self.assertIsNotNone(routine)
        self.assertEqual(routine.managed_by, "twitch.command")
        self.assertEqual(
            routine.tasks[0].task_type,
            SendTwitchChatMessageTask.task_type,
        )

        loaded = TwitchCommandTriggerStore(
            self.path,
            RoutineStore(self.routine_store.path),
        )
        loaded.load()
        saved = loaded.triggers[0]

        self.assertEqual(saved.name, "discord")
        self.assertEqual(saved.aliases, ["dc"])
        self.assertEqual(saved.permission, "subscriber")
        self.assertEqual(saved.uses, 1)
        self.assertTrue(saved.last_used_at)
        self.assertEqual(loaded.response_for(saved), "Join us, {user}!")
        loaded.update(
            saved.trigger_id,
            name="community",
            response="Community: {channel}",
            aliases=["discord"],
            permission="everyone",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.assertIsNotNone(loaded.resolve("discord"))
        self.assertEqual(
            loaded.response_for(saved),
            "Community: {channel}",
        )
        self.assertTrue(loaded.delete(saved.trigger_id))
        self.assertEqual(loaded.triggers, [])
        self.assertEqual(loaded.routine_store.routines, [])

    def test_version_one_direct_response_migrates_to_managed_routine(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "commands": [
                        {
                            "command_id": "legacy-1",
                            "name": "hello",
                            "response": "Hello {user}",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        self.store.load()

        trigger = self.store.triggers[0]
        self.assertEqual(trigger.trigger_id, "legacy-1")
        self.assertTrue(trigger.routine_id)
        self.assertEqual(self.store.response_for(trigger), "Hello {user}")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 3)
        self.assertIn("triggers", saved)

    def test_validation_rejects_collisions_reserved_names_and_bad_variables(self) -> None:
        self.store.add("discord", "Community link")
        with self.assertRaisesRegex(ValueError, "already used"):
            self.store.add("socials", "Links", aliases=["discord"])
        with self.assertRaisesRegex(ValueError, "built-in"):
            self.store.add("sallymemory", "No")
        with self.assertRaisesRegex(ValueError, "Unknown command variable"):
            self.store.add("broken", "Hello {username}")

    def test_command_edit_preserves_added_routine_tasks_after_reordering(self) -> None:
        trigger = self.store.add("hello", "Hello {user}")
        routine = self.routine_store.get(trigger.routine_id)
        primary_id = routine.tasks[0].task_id
        extra = self.routine_store.add_task(
            routine.routine_id,
            task_type="test.audit",
            name="Record command use",
            index=0,
        )

        self.store.update(
            trigger.trigger_id,
            name="hello",
            response="Welcome {user}",
            aliases=[],
            permission="everyone",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )

        saved = self.routine_store.get(trigger.routine_id)
        self.assertEqual([task.task_id for task in saved.tasks], [extra.task_id, primary_id])
        self.assertEqual(self.store.response_for(trigger), "Welcome {user}")

        loaded = TwitchCommandTriggerStore(
            self.path,
            RoutineStore(self.routine_store.path),
        )
        self.assertEqual(len(loaded.load()), 1)
        self.assertEqual(loaded.response_for(loaded.triggers[0]), "Welcome {user}")

    def test_command_response_accepts_variable_generated_by_another_task(self) -> None:
        trigger = self.store.add("random", "Placeholder")
        routine = self.routine_store.get(trigger.routine_id)
        response = routine.tasks[0]
        self.routine_store.add_task(
            routine.routine_id,
            task_type="core.file_random_line",
            name="Choose response",
            config={"path": "responses.txt", "variable": "random_line"},
            index=0,
        )
        self.routine_store.update_task(
            routine.routine_id,
            response.task_id,
            config={"message": "{random_line}", "as_bot": True},
        )
        self.store.save()

        loaded = TwitchCommandTriggerStore(
            self.path,
            RoutineStore(self.routine_store.path),
        )

        self.assertEqual(len(loaded.load()), 1)
        self.assertEqual(
            loaded.response_for(loaded.triggers[0]),
            "{random_line}",
        )

    def test_command_can_trigger_routine_without_chat_response(self) -> None:
        routine = self.routine_store.add("Toggle the lights")
        task = self.routine_store.add_task(
            routine.routine_id,
            task_type="core.open_path",
            name="Run lighting automation",
            config={"path": "lights.py"},
        )

        trigger = self.store.attach_routine(
            routine.routine_id,
            "lights",
            "",
        )

        attached = self.routine_store.get(routine.routine_id)
        self.assertEqual(attached.trigger_id, trigger.trigger_id)
        self.assertEqual([value.task_id for value in attached.tasks], [task.task_id])
        self.assertEqual(self.store.response_for(trigger), "")

        chat_task = self.routine_store.add_task(
            routine.routine_id,
            task_type=SendTwitchChatMessageTask.task_type,
            name="A normal automation task",
            config={"message": "This task is not the command response"},
        )
        self.store.update(
            trigger.trigger_id,
            name="lights",
            response="",
            aliases=[],
            permission="everyone",
            global_cooldown_seconds=10,
            user_cooldown_seconds=30,
        )
        self.assertEqual(self.store.response_for(trigger), "")
        self.assertIn(
            chat_task.task_id,
            [value.task_id for value in self.routine_store.get(routine.routine_id).tasks],
        )

        loaded = TwitchCommandTriggerStore(
            self.path,
            RoutineStore(self.routine_store.path),
        )
        self.assertEqual(len(loaded.load()), 1)
        self.assertEqual(loaded.response_for(loaded.triggers[0]), "")

    def test_clearing_response_removes_only_managed_response_task(self) -> None:
        trigger = self.store.add("hello", "Hello {user}")
        extra = self.routine_store.add_task(
            trigger.routine_id,
            task_type="core.delay",
            name="Wait",
            config={"seconds": 1},
        )

        self.store.update(
            trigger.trigger_id,
            name="hello",
            response="",
            aliases=[],
            permission="everyone",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )

        routine = self.routine_store.get(trigger.routine_id)
        self.assertEqual([task.task_id for task in routine.tasks], [extra.task_id])
        self.assertEqual(self.store.response_for(trigger), "")

        self.store.update(
            trigger.trigger_id,
            name="hello",
            response="Done, {user}",
            aliases=[],
            permission="everyone",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )

        self.assertEqual(self.store.response_for(trigger), "Done, {user}")
        self.assertEqual(len(self.routine_store.get(trigger.routine_id).tasks), 2)

    def test_trigger_can_attach_to_and_detach_from_existing_routine(self) -> None:
        routine = self.routine_store.add("Existing workflow")
        extra = self.routine_store.add_task(
            routine.routine_id,
            task_type="test.audit",
            name="Keep me",
        )

        trigger = self.store.attach_routine(
            routine.routine_id,
            "hello",
            "Hello {user}",
        )

        attached = self.routine_store.get(routine.routine_id)
        self.assertEqual(attached.trigger_id, trigger.trigger_id)
        self.assertEqual(attached.managed_by, self.store.MANAGED_BY)
        self.assertEqual(len(attached.tasks), 2)
        self.assertEqual(attached.tasks[1].task_id, extra.task_id)
        self.assertTrue(self.store.delete(trigger.trigger_id, delete_routine=False))
        detached = self.routine_store.get(routine.routine_id)
        self.assertIsNotNone(detached)
        self.assertEqual(detached.trigger_id, "")
        self.assertEqual(detached.managed_by, "")
        self.assertEqual(len(detached.tasks), 2)

    def test_command_update_preserves_a_custom_routine_name(self) -> None:
        trigger = self.store.add("hello", "Hello")
        self.routine_store.update(trigger.routine_id, name="Friendly Greeting")

        self.store.update(
            trigger.trigger_id,
            name="greet",
            response="Welcome",
            aliases=[],
            permission="everyone",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )

        self.assertEqual(
            self.routine_store.get(trigger.routine_id).name,
            "Friendly Greeting",
        )


class TwitchCommandTriggerDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = TwitchCommandTriggerStore(
            root / "commands.json",
            RoutineStore(root / "routines.json"),
        )
        self.now = 100.0
        self.dispatcher = TwitchCommandTriggerDispatcher(
            self.store,
            clock=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_general_trigger_context_and_records_only_after_execution(self) -> None:
        trigger = self.store.add(
            "uptime",
            "{user}: {channel} has streamed {uptime}; use #{uses}; target {target}; args {args}.",
            global_cooldown_seconds=10,
            user_cooldown_seconds=30,
        )
        incoming = message("!uptime @Someone extra words")
        result = self.dispatcher.evaluate(
            incoming,
            {"channel": "sally", "uptime": "01:02:03"},
        )

        self.assertEqual(result.outcome, TwitchCommandTriggerOutcome.READY)
        event = result.to_event()
        self.assertEqual(event.service, "twitch")
        self.assertEqual(event.trigger_type, "command")
        self.assertEqual(event.trigger_id, trigger.trigger_id)
        self.assertEqual(event.context["target"], "Someone")
        self.assertEqual(event.context["args"], "@Someone extra words")
        self.assertEqual(trigger.uses, 0)
        self.dispatcher.record_executed(result, incoming)
        self.assertEqual(trigger.uses, 1)
        self.assertEqual(
            self.dispatcher.evaluate(incoming).outcome,
            TwitchCommandTriggerOutcome.COOLDOWN,
        )

    def test_global_and_per_viewer_cooldowns_are_both_enforced(self) -> None:
        self.store.add(
            "hello",
            "Hello {user}",
            global_cooldown_seconds=5,
            user_cooldown_seconds=20,
        )
        first = message("!hello", user_id="one")
        second = message("!hello", user_id="two")
        result = self.dispatcher.evaluate(first)
        self.dispatcher.record_executed(result, first)
        self.now += 6
        self.assertEqual(self.dispatcher.evaluate(first).remaining_seconds, 14)
        self.assertEqual(
            self.dispatcher.evaluate(second).outcome,
            TwitchCommandTriggerOutcome.READY,
        )

    def test_permissions_follow_twitch_role_hierarchy(self) -> None:
        self.store.add(
            "modonly",
            "Approved",
            permission=TwitchCommandPermission.MODERATOR.value,
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.assertEqual(
            self.dispatcher.evaluate(message("!modonly")).outcome,
            TwitchCommandTriggerOutcome.DENIED,
        )
        moderator = message(
            "!modonly",
            badges=(TwitchBadge("moderator", "1", ""),),
        )
        self.assertEqual(
            self.dispatcher.evaluate(moderator).outcome,
            TwitchCommandTriggerOutcome.READY,
        )
        broadcaster = message("!modonly", user_id="broadcaster-1")
        self.assertEqual(
            self.dispatcher.evaluate(broadcaster).outcome,
            TwitchCommandTriggerOutcome.READY,
        )

    def test_unknown_disabled_and_non_command_outcomes_are_explicit(self) -> None:
        trigger = self.store.add("hello", "Hello")
        self.store.set_enabled(trigger.trigger_id, False)
        cases = (
            ("hello", TwitchCommandTriggerOutcome.NOT_A_COMMAND),
            ("!missing", TwitchCommandTriggerOutcome.NOT_FOUND),
            ("!", TwitchCommandTriggerOutcome.NOT_FOUND),
            ("!hello", TwitchCommandTriggerOutcome.DISABLED),
        )
        for text, outcome in cases:
            self.assertEqual(self.dispatcher.evaluate(message(text)).outcome, outcome)


if __name__ == "__main__":
    unittest.main()
