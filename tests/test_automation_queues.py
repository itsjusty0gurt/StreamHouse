from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from automation.control_tasks import register_control_tasks
from automation.queues import AutomationQueueManager, AutomationQueueStore
from automation.routines import RoutineStore
from automation.service import AutomationService
from automation.tasks import TaskRegistry


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
