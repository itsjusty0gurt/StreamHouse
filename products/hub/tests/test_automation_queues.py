from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from products.hub.automation.models import (
    DEFAULT_AUTOMATION_QUEUE_ID,
    DEFAULT_AUTOMATION_QUEUE_NAME,
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.control_tasks import register_control_tasks
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.twitch.automation_triggers import (
    KEYWORD_PHRASE_EVENT_TYPE,
    TwitchEventTriggerStore,
)
from products.hub.twitch.commands import TwitchCommandTriggerStore


class CaptureTask:
    task_type = "test.queue_capture"

    def __init__(self) -> None:
        self.users: list[str] = []

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        self.users.append(str(trigger.context.get("user", "")))
        return TaskExecutionResult(task.task_id, task.task_type, True, "Captured.")


class AutomationQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.queue_store = AutomationQueueStore(root / "queues.json")
        self.manager = AutomationQueueManager(self.queue_store)
        self.routine_store = RoutineStore(root / "routines.json")
        self.registry = TaskRegistry()
        self.capture = CaptureTask()
        self.registry.register(self.capture)
        register_control_tasks(
            self.registry,
            self.routine_store,
            self.queue_store,
            self.manager,
        )
        self.service = AutomationService(
            self.routine_store,
            self.registry,
            queue_manager=self.manager,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_queued_routine(self, queue_id: str, trigger_id: str = "trigger"):
        routine = self.routine_store.add(
            "Queued routine",
            trigger_id=trigger_id,
            queue_id=queue_id,
        )
        self.routine_store.add_task(
            routine.routine_id,
            task_type=self.capture.task_type,
            name="Capture",
        )
        return self.routine_store.get(routine.routine_id)

    def event(self, user: str = "Viewer") -> TriggerEvent:
        return TriggerEvent("trigger", "test", "event", {"user": user})

    def test_fresh_store_persists_one_stable_default_queue(self) -> None:
        queues = self.queue_store.load()

        self.assertTrue(self.queue_store.path.exists())
        self.assertEqual(len(queues), 1)
        self.assertEqual(queues[0].queue_id, DEFAULT_AUTOMATION_QUEUE_ID)
        self.assertEqual(queues[0].name, DEFAULT_AUTOMATION_QUEUE_NAME)

        reloaded = AutomationQueueStore(self.queue_store.path)
        reloaded.load()
        self.assertEqual(
            [queue.queue_id for queue in reloaded.queues],
            [DEFAULT_AUTOMATION_QUEUE_ID],
        )

    def test_obsolete_or_unversioned_queue_data_is_rejected(self) -> None:
        for payload in (
            {"queues": []},
            {"version": 0, "queues": []},
            {"version": "1", "queues": []},
            {"version": 2, "queues": []},
        ):
            with self.subTest(payload=payload):
                self.queue_store.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsupported Automation queue"):
                    self.queue_store.load()

    def test_current_queue_schema_does_not_invent_missing_ids(self) -> None:
        self.queue_store.path.write_text(
            json.dumps(
                {
                    "version": self.queue_store.VERSION,
                    "queues": [
                        {
                            "queue_id": DEFAULT_AUTOMATION_QUEUE_ID,
                            "name": DEFAULT_AUTOMATION_QUEUE_NAME,
                        },
                        {"name": "Missing identity"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "stable IDs"):
            self.queue_store.load()

    def test_default_queue_cannot_be_deleted_or_renamed(self) -> None:
        self.assertFalse(self.queue_store.delete(DEFAULT_AUTOMATION_QUEUE_ID))
        with self.assertRaisesRegex(ValueError, "cannot be renamed"):
            self.queue_store.update(
                DEFAULT_AUTOMATION_QUEUE_ID,
                name="Something Else",
            )
        self.assertEqual(
            self.queue_store.default().name,
            DEFAULT_AUTOMATION_QUEUE_NAME,
        )

    def test_unassigned_manual_routine_runs_through_default_queue(self) -> None:
        routine = self.routine_store.add("Default queued routine")
        self.routine_store.add_task(
            routine.routine_id,
            task_type=self.capture.task_type,
            name="Capture",
        )
        self.queue_store.update(DEFAULT_AUTOMATION_QUEUE_ID, paused=True)

        result = self.service.run_routine(routine.routine_id, {"user": "Queued"})

        self.assertEqual(routine.queue_id, DEFAULT_AUTOMATION_QUEUE_ID)
        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.users, [])
        self.assertEqual(self.manager.count(DEFAULT_AUTOMATION_QUEUE_ID), 1)

        self.queue_store.update(DEFAULT_AUTOMATION_QUEUE_ID, paused=False)
        self.service.process_queues()
        self.assertEqual(self.capture.users, ["Queued"])

    def test_multiple_unassigned_routines_share_default_queue_order(self) -> None:
        self.queue_store.update(DEFAULT_AUTOMATION_QUEUE_ID, paused=True)
        for name in ("First routine", "Second routine"):
            routine = self.routine_store.add(name, trigger_id="shared")
            self.routine_store.add_task(
                routine.routine_id,
                task_type=self.capture.task_type,
                name="Capture",
            )

        self.service.publish_trigger(
            TriggerEvent("shared", "test", "event", {"user": "Viewer"})
        )
        self.assertEqual(self.manager.count(DEFAULT_AUTOMATION_QUEUE_ID), 2)

        self.queue_store.update(DEFAULT_AUTOMATION_QUEUE_ID, paused=False)
        self.service.process_queues()
        self.service.process_queues()
        self.assertEqual(self.capture.users, ["Viewer", "Viewer"])

    def test_missing_custom_queue_assignment_normalizes_to_default(self) -> None:
        custom = self.queue_store.add("Alerts")
        routine = self.routine_store.add("Alert", queue_id=custom.queue_id)
        self.queue_store.delete(custom.queue_id)

        changed = self.routine_store.normalize_queue_assignments(
            queue.queue_id for queue in self.queue_store.queues
        )

        self.assertEqual(changed, 1)
        self.assertEqual(
            self.routine_store.get(routine.routine_id).queue_id,
            DEFAULT_AUTOMATION_QUEUE_ID,
        )

    def test_blank_pre_alpha_queue_assignment_is_rewritten_to_default(self) -> None:
        self.routine_store.path.write_text(
            json.dumps(
                {
                    "version": self.routine_store.VERSION,
                    "groups": [],
                    "routines": [
                        {
                            "routine_id": "old-routine",
                            "name": "Old routine",
                            "trigger_id": "old-trigger",
                            "tasks": [],
                            "queue_id": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        self.routine_store.load()

        self.assertEqual(
            self.routine_store.get("old-routine").queue_id,
            DEFAULT_AUTOMATION_QUEUE_ID,
        )
        saved = json.loads(self.routine_store.path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["routines"][0]["queue_id"],
            DEFAULT_AUTOMATION_QUEUE_ID,
        )

    def test_command_keyword_and_ads_routines_use_default_queue(self) -> None:
        command_store = TwitchCommandTriggerStore(
            Path(self.temporary.name) / "commands.json",
            self.routine_store,
        )
        command = command_store.configure_default("uptime")
        event_store = TwitchEventTriggerStore(
            Path(self.temporary.name) / "events.json",
            self.routine_store,
        )
        keyword_routine = self.routine_store.add("Keyword")
        event_store.add(
            keyword_routine.routine_id,
            KEYWORD_PHRASE_EVENT_TYPE,
            filters={"phrase": "coffee", "match_type": "contains"},
        )
        ads_routine = self.routine_store.add("Ads Started")
        event_store.add(ads_routine.routine_id, "ads.started")

        self.assertEqual(
            self.routine_store.get(command.routine_id).queue_id,
            DEFAULT_AUTOMATION_QUEUE_ID,
        )
        self.assertEqual(
            self.routine_store.get(keyword_routine.routine_id).queue_id,
            DEFAULT_AUTOMATION_QUEUE_ID,
        )
        self.assertEqual(
            self.routine_store.get(ads_routine.routine_id).queue_id,
            DEFAULT_AUTOMATION_QUEUE_ID,
        )

    def test_queue_settings_and_routine_assignment_persist(self) -> None:
        queue = self.queue_store.add(
            "Soundboard",
            max_length=25,
            duplicate_policy="replace",
            delay_seconds=1.5,
        )
        routine = self.add_queued_routine(queue.queue_id)

        loaded_queues = AutomationQueueStore(self.queue_store.path)
        loaded_queues.load()
        loaded_routines = RoutineStore(self.routine_store.path)
        loaded_routines.load()

        self.assertEqual(loaded_queues.get(queue.queue_id).duplicate_policy, "replace")
        self.assertEqual(loaded_queues.get(queue.queue_id).delay_seconds, 1.5)
        self.assertEqual(loaded_routines.get(routine.routine_id).queue_id, queue.queue_id)

    def test_paused_queue_collects_then_processes_in_order(self) -> None:
        queue = self.queue_store.add("Alerts")
        self.queue_store.update(queue.queue_id, paused=True)
        self.add_queued_routine(queue.queue_id)

        first = self.service.publish_trigger(self.event("First"))
        second = self.service.publish_trigger(self.event("Second"))

        self.assertTrue(first.succeeded)
        self.assertIn("Queued", first.routine_results[0].detail)
        self.assertEqual(self.capture.users, [])
        self.assertEqual(self.manager.count(queue.queue_id), 2)

        self.queue_store.update(queue.queue_id, paused=False)
        self.service.process_queues()
        self.service.process_queues()

        self.assertEqual(self.capture.users, ["First", "Second"])
        self.assertEqual(self.manager.count(queue.queue_id), 0)

    def test_ignore_duplicate_policy_keeps_one_pending_copy(self) -> None:
        queue = self.queue_store.add("Chat", duplicate_policy="ignore")
        self.queue_store.update(queue.queue_id, paused=True)
        self.add_queued_routine(queue.queue_id)

        self.service.publish_trigger(self.event("First"))
        duplicate = self.service.publish_trigger(self.event("Second"))

        self.assertTrue(duplicate.succeeded)
        self.assertIn("Ignored duplicate", duplicate.routine_results[0].detail)
        self.assertEqual(self.manager.count(queue.queue_id), 1)

    def test_replace_duplicate_policy_keeps_newest_context(self) -> None:
        queue = self.queue_store.add("Chat", duplicate_policy="replace")
        self.queue_store.update(queue.queue_id, paused=True)
        self.add_queued_routine(queue.queue_id)

        self.service.publish_trigger(self.event("Old"))
        self.service.publish_trigger(self.event("New"))
        self.queue_store.update(queue.queue_id, paused=False)
        self.service.process_queues()

        self.assertEqual(self.capture.users, ["New"])

    def test_pending_items_can_be_reordered_and_removed(self) -> None:
        queue = self.queue_store.add("Order")
        first = self.manager.enqueue(queue.queue_id, "a", "A", self.event("A")).item
        second = self.manager.enqueue(queue.queue_id, "b", "B", self.event("B")).item

        self.manager.reorder(queue.queue_id, [second.item_id, first.item_id])
        self.assertEqual(
            [item.routine_name for item in self.manager.pending[queue.queue_id]],
            ["B", "A"],
        )
        self.assertTrue(self.manager.remove(queue.queue_id, second.item_id))
        self.assertEqual(self.manager.count(queue.queue_id), 1)

    def test_queue_delay_blocks_next_item_until_ready(self) -> None:
        queue = self.queue_store.add("Delayed", delay_seconds=2)
        self.manager.enqueue(queue.queue_id, "a", "A", self.event())
        self.manager.enqueue(queue.queue_id, "b", "B", self.event())
        first = self.manager.take_ready(queue.queue_id, now=10)
        self.assertIsNotNone(first)
        self.manager.complete(queue.queue_id, now=10)

        self.assertIsNone(self.manager.take_ready(queue.queue_id, now=11.9))
        self.assertIsNotNone(self.manager.take_ready(queue.queue_id, now=12))

    def test_routine_and_task_state_tasks_toggle_enabled_state(self) -> None:
        routine = self.routine_store.add("Toggle me")
        target_task = self.routine_store.add_task(
            routine.routine_id,
            task_type=self.capture.task_type,
            name="Capture",
        )
        controller = self.routine_store.add("Controller")
        self.routine_store.add_task(
            controller.routine_id,
            task_type="core.set_task_state",
            name="Disable capture",
            config={
                "routine_id": routine.routine_id,
                "task_id": target_task.task_id,
                "action": "disable",
            },
        )
        self.routine_store.add_task(
            controller.routine_id,
            task_type="core.set_routine_state",
            name="Disable routine",
            config={"routine_id": routine.routine_id, "action": "disable"},
        )

        result = self.service.run_routine(controller.routine_id)

        self.assertTrue(result.succeeded)
        updated = self.routine_store.get(routine.routine_id)
        self.assertFalse(updated.enabled)
        self.assertFalse(updated.tasks[0].enabled)

    def test_queue_control_tasks_pause_and_clear_pending_items(self) -> None:
        queue = self.queue_store.add("Controlled")
        self.manager.enqueue(queue.queue_id, "a", "A", self.event("A"))
        controller = self.routine_store.add("Queue controller")
        self.routine_store.add_task(
            controller.routine_id,
            task_type="core.set_queue_state",
            name="Pause queue",
            config={"queue_id": queue.queue_id, "action": "pause"},
        )
        self.routine_store.add_task(
            controller.routine_id,
            task_type="core.clear_queue",
            name="Clear queue",
            config={"queue_id": queue.queue_id},
        )

        result = self.service.run_routine(controller.routine_id)

        self.assertTrue(result.succeeded)
        self.assertTrue(self.queue_store.get(queue.queue_id).paused)
        self.assertEqual(self.manager.count(queue.queue_id), 0)


if __name__ == "__main__":
    unittest.main()
