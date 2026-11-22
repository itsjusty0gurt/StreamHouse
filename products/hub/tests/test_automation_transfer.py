from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.models import DEFAULT_AUTOMATION_QUEUE_ID, TaskDefinition
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.transfer import export_routine, import_routine, validate_import
from products.hub.obs_service.triggers import ObsTriggerStore
from products.hub.integrations.music_triggers import MusicTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.commands import TwitchCommandTriggerStore


class AvailableTask:
    task_type = "core.wait"

    def execute(self, task, trigger):  # pragma: no cover - registry marker only
        raise AssertionError("Import validation must not execute tasks.")


class AvailableIfTask:
    task_type = "core.if"

    def execute(self, task, trigger):  # pragma: no cover - registry marker only
        raise AssertionError("Import validation must not execute tasks.")


class AutomationTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = TaskRegistry()
        self.registry.register(AvailableTask())
        self.registry.register(AvailableIfTask())
        self.stores = self.make_stores(self.root / "source")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def make_stores(root: Path):
        routines = RoutineStore(root / "routines.json")
        return {
            "routine_store": routines,
            "command_store": TwitchCommandTriggerStore(root / "commands.json", routines),
            "event_store": TwitchEventTriggerStore(root / "events.json", routines),
            "core_store": CoreTriggerStore(root / "core.json", routines),
            "obs_store": ObsTriggerStore(root / "obs.json", routines),
            "music_store": MusicTriggerStore(root / "music.json", routines),
        }

    def test_routine_round_trip_regenerates_ids_and_preserves_structure(self) -> None:
        group = self.stores["routine_store"].add_group("Stream")
        routine = self.stores["routine_store"].add(
            "Start Show",
            group_id=group.group_id,
            description="Prepare everything",
        )
        original_task = self.stores["routine_store"].add_task(
            routine.routine_id,
            task_type="core.wait",
            name="Brief wait",
            config={"duration": "2", "unit": "seconds"},
        )
        self.stores["core_store"].add(routine.routine_id, "application.started")
        self.stores["obs_store"].add(
            routine.routine_id,
            "CurrentProgramSceneChanged",
            filters={"sceneName": "Gameplay"},
        )
        payload = export_routine(
            self.stores["routine_store"].get(routine.routine_id),
            **self.stores,
        )
        self.assertEqual(payload["format"], "streamhouse.automation.routine")
        destination = self.make_stores(self.root / "destination")
        imported_group = destination["routine_store"].add_group("Stream")

        imported = import_routine(
            payload,
            group_id=imported_group.group_id,
            task_registry=self.registry,
            **destination,
        )

        self.assertNotEqual(imported.routine_id, routine.routine_id)
        self.assertEqual(imported.name, routine.name)
        self.assertEqual(imported.description, routine.description)
        self.assertEqual(imported.queue_id, DEFAULT_AUTOMATION_QUEUE_ID)
        self.assertEqual(
            imported.tasks[0].config,
            {"duration": "2", "unit": "seconds"},
        )
        self.assertNotEqual(imported.tasks[0].task_id, original_task.task_id)
        self.assertEqual(len(destination["core_store"].for_routine(imported.routine_id)), 1)
        self.assertEqual(len(destination["obs_store"].for_routine(imported.routine_id)), 1)

    def test_system_triggers_transfer_with_new_ids_and_require_explicit_enable(self) -> None:
        routine = self.stores["routine_store"].add("System Triggers")
        configurations = {
            "scheduled_time": {"mode": "simple", "expression": "30 8 * * 1,5"},
            "hotkey": {"shortcut": "Ctrl+Alt+H"},
            "process_started": {"name": "notepad.exe"},
            "process_stopped": {"name": "notepad.exe"},
            "file_watcher": {"kind": "file", "event": "modified", "path": str(self.root / "test.txt")},
        }
        core = self.stores["core_store"]
        originals = {
            key: core.add(routine.routine_id, key, system_config=config)
            for key, config in configurations.items()
        }
        payload = export_routine(self.stores["routine_store"].get(routine.routine_id), **self.stores)
        destination = self.make_stores(self.root / "destination")
        imported = import_routine(payload, group_id="", task_registry=self.registry, **destination)
        restored = destination["core_store"].for_routine(imported.routine_id)
        self.assertEqual(len(restored), len(configurations))
        for trigger in restored:
            with self.subTest(trigger=trigger.event_type):
                self.assertNotEqual(trigger.trigger_id, originals[trigger.event_type].trigger_id)
                self.assertFalse(trigger.enabled)
                self.assertEqual(trigger.system_config, configurations[trigger.event_type])

    def test_custom_event_transfer_keeps_configuration_but_requires_trigger_relink(self) -> None:
        for task_type in ("core.send_custom_event", "core.set_trigger_state", "core.log_message"):
            marker = AvailableTask()
            marker.task_type = task_type
            self.registry.register(marker)
        store = self.stores["routine_store"]
        routine = store.add("Event test")
        trigger = self.stores["core_store"].add(
            routine.routine_id, "custom_event", event_name="Break Started", enabled=False,
        )
        tasks = (
            ("core.send_custom_event", {"event_name": "Break Started", "event_data": "{user.name}"}),
            ("core.set_trigger_state", {"routine_id": routine.routine_id, "trigger_id": trigger.trigger_id}),
            ("core.log_message", {"message": "Event received\n{core.custom_event.data}", "level": "info"}),
        )
        for task_type, config in tasks:
            store.add_task(routine.routine_id, task_type=task_type, name=task_type, config=config)
        payload = export_routine(store.get(routine.routine_id), **self.stores)
        # Same-install import has an existing exact ID. It must still not bind implicitly.
        imported = import_routine(payload, group_id="", task_registry=self.registry, **self.stores)
        [copied] = self.stores["core_store"].for_routine(imported.routine_id)
        self.assertNotEqual(copied.trigger_id, trigger.trigger_id)
        self.assertEqual((copied.event_name, copied.enabled), ("Break Started", False))
        self.assertEqual(imported.tasks[0].config, tasks[0][1])
        self.assertEqual(imported.tasks[1].config, {**tasks[1][1], "requires_relink": True})
        self.assertEqual(imported.tasks[2].config, tasks[2][1])

    def test_import_rejects_unavailable_task_before_creating_routine(self) -> None:
        payload = {
            "format": "streamhouse.automation.routine",
            "version": 2,
            "routine": {
                "name": "Unavailable",
                "tasks": [
                    {"task_type": "missing.task", "name": "Missing", "config": {}}
                ],
            },
            "triggers": {},
        }

        with self.assertRaisesRegex(ValueError, "Unavailable task provider"):
            validate_import(
                payload,
                task_registry=self.registry,
                command_store=self.stores["command_store"],
            )
        self.assertEqual(self.stores["routine_store"].routines, [])

    def test_nested_if_tasks_round_trip_with_new_ids(self) -> None:
        routine = self.stores["routine_store"].add("Conditional")
        condition = self.stores["routine_store"].add_task(
            routine.routine_id,
            task_type="core.if",
            name="If coffee",
            config={"left": "{command.data}", "operator": "contains", "right": "coffee"},
            then_tasks=[
                TaskDefinition(
                    "then-wait",
                    "core.wait",
                    "Then wait",
                    {"duration": "1", "unit": "seconds"},
                )
            ],
            else_tasks=[
                TaskDefinition(
                    "else-wait",
                    "core.wait",
                    "Else wait",
                    {"duration": "2", "unit": "seconds"},
                )
            ],
        )
        payload = export_routine(
            self.stores["routine_store"].get(routine.routine_id),
            **self.stores,
        )
        destination = self.make_stores(self.root / "nested-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        imported_if = imported.tasks[0]
        self.assertEqual(payload["version"], 2)
        self.assertNotEqual(imported_if.task_id, condition.task_id)
        self.assertNotEqual(imported_if.then_tasks[0].task_id, "then-wait")
        self.assertNotEqual(imported_if.else_tasks[0].task_id, "else-wait")
        self.assertEqual(imported_if.then_tasks[0].config["duration"], "1")

    def test_user_group_reference_is_preserved_as_unresolved_stable_id(self) -> None:
        routine = self.stores["routine_store"].add("Auto shoutout")
        stable_group_id = "custom-group-stable-id"
        self.stores["routine_store"].add_task(
            routine.routine_id,
            task_type="core.if",
            name="Known streamer",
            config={"operator": "user_in_group", "group_id": stable_group_id},
        )

        payload = export_routine(
            self.stores["routine_store"].get(routine.routine_id),
            **self.stores,
        )
        destination = self.make_stores(self.root / "group-reference-destination")
        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        condition = imported.tasks[0].config["conditions"][0]
        self.assertEqual(condition["operator"], "user_in_group")
        self.assertEqual(condition["group_id"], stable_group_id)

    def test_timer_trigger_round_trip_preserves_schedule_configuration(self) -> None:
        routine = self.stores["routine_store"].add("Random promo")
        self.stores["core_store"].add_timer(
            routine.routine_id,
            timer_mode="random",
            timer_minimum="30",
            timer_minimum_unit="minutes",
            timer_maximum="60",
            timer_maximum_unit="minutes",
        )
        payload = export_routine(routine, **self.stores)
        destination = self.make_stores(self.root / "timer-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        saved = destination["core_store"].for_routine(imported.routine_id)[0]
        self.assertEqual(saved.event_type, "timer")
        self.assertEqual(saved.timer_mode, "random")
        self.assertEqual(saved.timer_minimum, "30")
        self.assertEqual(saved.timer_maximum, "60")

    def test_if_trigger_round_trip_preserves_conditions_and_mode(self) -> None:
        routine = self.stores["routine_store"].add("Scene guard")
        self.stores["core_store"].add_if(
            routine.routine_id,
            condition_join="or",
            conditions=[
                {
                    "left": "{obs.current_scene}",
                    "operator": "equals",
                    "right": "Game",
                    "ignore_case": False,
                    "group_id": "",
                },
                {
                    "left": "{custom.mode}",
                    "operator": "equals",
                    "right": "live",
                    "ignore_case": True,
                    "group_id": "",
                },
            ],
            firing_mode="changes",
        )
        payload = export_routine(routine, **self.stores)
        destination = self.make_stores(self.root / "if-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )
        saved = destination["core_store"].for_routine(imported.routine_id)[0]

        self.assertEqual(saved.event_type, "if")
        self.assertEqual(saved.condition_join, "or")
        self.assertEqual(len(saved.conditions), 2)
        self.assertEqual(saved.firing_mode, "changes")

    def test_music_trigger_round_trip_preserves_event_and_enabled_state(self) -> None:
        routine = self.stores["routine_store"].add("Now Playing")
        self.stores["music_store"].add(
            routine.routine_id,
            "track.changed",
            enabled=False,
        )
        payload = export_routine(routine, **self.stores)
        destination = self.make_stores(self.root / "music-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        triggers = destination["music_store"].for_routine(imported.routine_id)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].event_type, "track.changed")
        self.assertFalse(triggers[0].enabled)

    def test_outgoing_raid_completed_trigger_round_trip_preserves_event(self) -> None:
        routine = self.stores["routine_store"].add("Raid completed")
        original = self.stores["event_store"].add(
            routine.routine_id,
            "channel.raid.outgoing",
        )
        payload = export_routine(routine, **self.stores)
        destination = self.make_stores(self.root / "raid-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        triggers = destination["event_store"].for_routine(imported.routine_id)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].event_type, "channel.raid.outgoing")
        self.assertNotEqual(triggers[0].trigger_id, original.trigger_id)

    def test_moderation_trigger_round_trip_preserves_filters_and_enabled_state(self) -> None:
        routine = self.stores["routine_store"].add("Approved unban request")
        original = self.stores["event_store"].add(
            routine.routine_id,
            "channel.unban_request.resolve",
            filters={"user_login": "viewer", "status": "approved"},
            enabled=False,
        )
        payload = export_routine(routine, **self.stores)
        destination = self.make_stores(self.root / "moderation-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        triggers = destination["event_store"].for_routine(imported.routine_id)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(
            triggers[0].event_type,
            "channel.unban_request.resolve",
        )
        self.assertEqual(
            triggers[0].filters,
            {"user_login": "viewer", "status": "approved"},
        )
        self.assertFalse(triggers[0].enabled)
        self.assertNotEqual(triggers[0].trigger_id, original.trigger_id)

    def test_import_detects_command_conflict(self) -> None:
        command = self.stores["command_store"].add("hello", "Hello!")
        routine = self.stores["routine_store"].get(command.routine_id)
        payload = export_routine(routine, **self.stores)

        with self.assertRaisesRegex(ValueError, "already exists"):
            validate_import(
                payload,
                task_registry=self.registry,
                command_store=self.stores["command_store"],
            )

    def test_command_routine_round_trip_rebuilds_managed_trigger_task(self) -> None:
        command = self.stores["command_store"].add(
            "discord",
            "Join us, {user.display_name}!",
            aliases=["community"],
        )
        self.stores["routine_store"].add_task(
            command.routine_id,
            task_type="core.wait",
            name="Small delay",
            config={"duration": "1", "unit": "seconds"},
        )
        routine = self.stores["routine_store"].get(command.routine_id)
        payload = export_routine(routine, **self.stores)
        destination = self.make_stores(self.root / "command-destination")

        imported = import_routine(
            payload,
            group_id="",
            task_registry=self.registry,
            **destination,
        )

        imported_command = destination["command_store"].for_routine(
            imported.routine_id
        )
        self.assertEqual(imported_command.name, "discord")
        self.assertEqual(imported_command.aliases, ["community"])
        self.assertEqual(
            destination["command_store"].response_for(imported_command),
            "Join us, {user.display_name}!",
        )
        self.assertEqual(len(imported.tasks), 2)
        self.assertEqual(imported.tasks[0].managed_key, "twitch.command")
        self.assertEqual(imported.tasks[1].task_type, "core.wait")


if __name__ == "__main__":
    unittest.main()
