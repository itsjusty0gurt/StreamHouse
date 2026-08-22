from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.transfer import export_routine, import_routine, validate_import
from products.hub.obs_service.triggers import ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.commands import TwitchCommandTriggerStore


class AvailableTask:
    task_type = "core.delay"

    def execute(self, task, trigger):  # pragma: no cover - registry marker only
        raise AssertionError("Import validation must not execute tasks.")


class AutomationTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = TaskRegistry()
        self.registry.register(AvailableTask())
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
            task_type="core.delay",
            name="Brief wait",
            config={"seconds": 2},
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
        self.assertEqual(imported.tasks[0].config, {"seconds": 2})
        self.assertNotEqual(imported.tasks[0].task_id, original_task.task_id)
        self.assertEqual(len(destination["core_store"].for_routine(imported.routine_id)), 1)
        self.assertEqual(len(destination["obs_store"].for_routine(imported.routine_id)), 1)

    def test_import_rejects_unavailable_task_before_creating_routine(self) -> None:
        payload = {
            "format": "streamhouse.automation.routine",
            "version": 1,
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
            task_type="core.delay",
            name="Small delay",
            config={"seconds": 1},
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
        self.assertEqual(imported.tasks[1].task_type, "core.delay")


if __name__ == "__main__":
    unittest.main()
