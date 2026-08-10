from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from products.hub.automation.routines import RoutineStore
from products.hub.twitch.commands import (
    TwitchCommandPermission,
    TwitchCommandSetupState,
    TwitchCommandTriggerDispatcher,
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerStore,
)
from products.hub.twitch.channel_information import (
    ChannelInformation,
    ChannelInformationStore,
    SocialLink,
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
        self.assertEqual(saved["version"], 4)
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

    def test_defaults_seed_once_without_overwriting_customizations(self) -> None:
        first = self.store.seed_default_commands()
        self.assertEqual(
            set(first.created),
            {
                "uptime", "followage", "accountage", "title", "game", "commands",
                "discord", "socials", "youtube", "schedule", "rules", "server",
            },
        )
        uptime = self.store.resolve("uptime")
        self.store.update(
            uptime.trigger_id,
            name="up",
            response="My custom response",
            aliases=["uptime"],
            permission="subscriber",
            global_cooldown_seconds=42,
            user_cooldown_seconds=84,
        )

        loaded = TwitchCommandTriggerStore(
            self.path,
            RoutineStore(self.routine_store.path),
        )
        loaded.load()
        second = loaded.seed_default_commands()

        self.assertEqual(second.created, ())
        customized = loaded.default("uptime")
        self.assertEqual(customized.name, "up")
        self.assertEqual(customized.aliases, ["uptime"])
        self.assertEqual(customized.permission, "subscriber")
        self.assertEqual(customized.global_cooldown_seconds, 42)
        self.assertEqual(loaded.response_for(customized), "My custom response")

    def test_deleted_default_stays_deleted_until_explicit_restore(self) -> None:
        self.store.seed_default_commands()
        command = self.store.default("game")
        self.assertTrue(self.store.delete(command.trigger_id))

        loaded = TwitchCommandTriggerStore(
            self.path,
            RoutineStore(self.routine_store.path),
        )
        loaded.load()
        self.assertEqual(loaded.seed_default_commands().created, ())
        self.assertIsNone(loaded.default("game"))

        restored = loaded.restore_default_commands()
        self.assertEqual(restored.created, ("game",))
        self.assertIsNotNone(loaded.default("game"))

    def test_default_name_conflict_is_reported_and_not_overwritten(self) -> None:
        custom = self.store.add("uptime", "Custom uptime")

        result = self.store.seed_default_commands()

        self.assertTrue(any("!uptime" in conflict for conflict in result.conflicts))
        self.assertIs(self.store.resolve("uptime"), custom)
        self.assertIsNone(self.store.default("uptime"))

    def test_reset_default_restores_current_trigger_and_routine_definition(self) -> None:
        self.store.seed_default_commands()
        command = self.store.default("title")
        self.store.update(
            command.trigger_id,
            name="headline",
            response="Customized",
            aliases=["title"],
            permission="moderator",
            global_cooldown_seconds=99,
            user_cooldown_seconds=100,
        )
        self.routine_store.add_task(
            command.routine_id,
            task_type="core.delay",
            name="Extra task",
            config={"seconds": 1},
        )

        reset = self.store.reset_default("title")

        self.assertEqual(reset.name, "title")
        self.assertEqual(reset.aliases, [])
        self.assertEqual(reset.permission, "everyone")
        self.assertEqual(
            [task.task_type for task in self.routine_store.get(reset.routine_id).tasks],
            [
                "twitch.get_channel_information",
                "core.select_text",
                "twitch.send_chat_message",
            ],
        )

    def test_configured_defaults_start_disabled_and_derive_setup_states(self) -> None:
        self.store.seed_default_commands()
        information_store = ChannelInformationStore(
            Path(self.temporary.name) / "channel-information.json"
        )
        information_store.load()
        for default_id in (
            "discord", "socials", "youtube", "schedule", "rules", "server"
        ):
            command = self.store.default(default_id)
            with self.subTest(default_id=default_id):
                self.assertFalse(command.enabled)
                self.assertEqual(
                    self.store.setup_state(command, information_store),
                    TwitchCommandSetupState.SETUP_REQUIRED,
                )
        discord = self.store.default("discord")
        information = ChannelInformation()
        information.social_links["discord"] = SocialLink(
            False, "https://discord.gg/example"
        )
        information_store.save(information)
        self.assertEqual(
            self.store.setup_state(discord, information_store),
            TwitchCommandSetupState.READY_DISABLED,
        )
        self.store.set_enabled(discord.trigger_id, True)
        self.assertEqual(
            self.store.setup_state(discord, information_store),
            TwitchCommandSetupState.ENABLED,
        )
        information.social_links["discord"] = SocialLink(False, "")
        information_store.save(information)
        self.assertEqual(
            self.store.setup_state(discord, information_store),
            TwitchCommandSetupState.CONFIGURATION_ERROR,
        )

    def test_default_order_precedes_customs_even_after_rename_disable_and_filter(self) -> None:
        self.store.add("zebra", "Z")
        self.store.seed_default_commands()
        self.store.add("alpha", "A")
        discord = self.store.default("discord")
        self.store.update(
            discord.trigger_id,
            name="community",
            response=self.store.response_for(discord),
            aliases=["discord"],
            permission=discord.permission,
            global_cooldown_seconds=discord.global_cooldown_seconds,
            user_cooldown_seconds=discord.user_cooldown_seconds,
        )

        ordered = self.store.ordered_triggers()
        self.assertEqual(
            [command.default_id for command in ordered[:12]],
            [
                "uptime", "followage", "accountage", "title", "game", "commands",
                "discord", "socials", "youtube", "schedule", "rules", "server",
            ],
        )
        self.assertEqual([command.name for command in ordered[12:]], ["alpha", "zebra"])
        filtered = self.store.ordered_triggers("a")
        first_custom = next(
            (index for index, command in enumerate(filtered) if not command.is_default),
            len(filtered),
        )
        self.assertTrue(all(command.is_default for command in filtered[:first_custom]))
        self.assertTrue(all(not command.is_default for command in filtered[first_custom:]))

    def test_reset_configured_default_restores_disabled_two_task_routine(self) -> None:
        self.store.seed_default_commands()
        discord = self.store.default("discord")
        self.store.set_enabled(discord.trigger_id, True)
        self.routine_store.add_task(
            discord.routine_id,
            task_type="core.delay",
            name="Extra task",
            config={"seconds": 1},
        )

        reset = self.store.reset_default("discord")

        self.assertFalse(reset.enabled)
        self.assertEqual(reset.name, "discord")
        self.assertEqual(
            [task.task_type for task in self.routine_store.get(reset.routine_id).tasks],
            [
                "twitch.get_channel_information_field",
                "twitch.send_chat_message",
            ],
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
        incoming = message(
            "!uptime @Someone extra words",
            badges=(
                TwitchBadge("moderator", "1", ""),
                TwitchBadge("subscriber", "12", ""),
            ),
        )
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
        self.assertEqual(event.context["user_is_mod"], "true")
        self.assertEqual(event.context["user_is_subscriber"], "true")
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

    def test_enabled_configured_default_is_rejected_before_tasks_when_data_is_missing(self) -> None:
        self.store.seed_default_commands()
        discord = self.store.default("discord")
        self.store.set_enabled(discord.trigger_id, True)
        information = ChannelInformationStore(
            Path(self.temporary.name) / "channel-information.json"
        )
        information.load()
        dispatcher = TwitchCommandTriggerDispatcher(
            self.store,
            channel_information=information,
        )

        self.assertEqual(
            dispatcher.evaluate(message("!discord")).outcome,
            TwitchCommandTriggerOutcome.CONFIGURATION_ERROR,
        )
        configured = ChannelInformation()
        configured.social_links["discord"] = SocialLink(
            False, "https://discord.gg/example"
        )
        information.save(configured)
        self.assertEqual(
            dispatcher.evaluate(message("!discord")).outcome,
            TwitchCommandTriggerOutcome.READY,
        )


if __name__ == "__main__":
    unittest.main()
