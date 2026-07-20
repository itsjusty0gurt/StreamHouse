from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation.models import (
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from automation.routines import RoutineStore
from automation.service import AutomationService
from automation.tasks import TaskRegistry
from core.events import Events
from twitch.tasks import SendTwitchChatMessageTask


class ExampleTask:
    task_type = "test.example"

    def __init__(self, succeeded: bool = True) -> None:
        self.succeeded = succeeded
        self.calls: list[tuple[TaskDefinition, TriggerEvent]] = []

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        self.calls.append((task, trigger))
        return TaskExecutionResult(
            task_id=task.task_id,
            task_type=task.task_type,
            succeeded=self.succeeded,
            detail="example",
        )


class AutomationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        Events.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.store = RoutineStore(Path(self.temporary.name) / "routines.json")
        self.registry = TaskRegistry()

    def tearDown(self) -> None:
        Events.clear()
        self.temporary.cleanup()

    def test_trigger_runs_matching_routine_and_publishes_lifecycle_events(self) -> None:
        handler = ExampleTask()
        self.registry.register(handler)
        self.store.create_managed(
            trigger_id="trigger-1",
            name="Example routine",
            managed_by="test",
            task_type=handler.task_type,
            task_name="Example task",
            task_config={"value": 1},
        )
        observed: list[str] = []
        Events.subscribe(
            "trigger_fired",
            lambda trigger: observed.append(
                f"trigger:{trigger.service}.{trigger.trigger_type}"
            ),
        )
        Events.subscribe(
            "trigger_fired.twitch.command",
            lambda **_values: observed.append("trigger:typed"),
        )
        Events.subscribe(
            "task_completed",
            lambda **_values: observed.append("task:completed"),
        )
        trigger = TriggerEvent(
            trigger_id="trigger-1",
            service="twitch",
            trigger_type="command",
            context={"user": "Viewer"},
        )

        result = AutomationService(self.store, self.registry).publish_trigger(trigger)

        self.assertTrue(result.handled)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(
            observed,
            ["trigger:twitch.command", "trigger:typed", "task:completed"],
        )

    def test_missing_task_provider_fails_routine_without_crashing(self) -> None:
        self.store.create_managed(
            trigger_id="trigger-1",
            name="Missing provider",
            managed_by="test",
            task_type="missing.task",
            task_name="Unavailable task",
            task_config={},
        )

        result = AutomationService(self.store, self.registry).publish_trigger(
            TriggerEvent(
                trigger_id="trigger-1",
                service="test",
                trigger_type="example",
                context={},
            )
        )

        self.assertTrue(result.handled)
        self.assertFalse(result.succeeded)
        self.assertIn(
            "No task provider",
            result.routine_results[0].task_results[0].detail,
        )

    def test_manual_run_executes_one_selected_routine(self) -> None:
        handler = ExampleTask()
        self.registry.register(handler)
        routine = self.store.add("Manual")
        self.store.add_task(
            routine.routine_id,
            task_type=handler.task_type,
            name="Run manually",
        )

        result = AutomationService(self.store, self.registry).run_routine(
            routine.routine_id,
            {"user": "Tester"},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(handler.calls[0][1].service, "sally")
        self.assertEqual(handler.calls[0][1].context["user"], "Tester")
        self.assertGreaterEqual(result.routine_results[0].task_results[0].duration_ms, 0)

    def test_single_task_run_does_not_execute_other_routine_tasks(self) -> None:
        handler = ExampleTask()
        self.registry.register(handler)
        routine = self.store.add("Two tasks")
        first = self.store.add_task(
            routine.routine_id,
            task_type=handler.task_type,
            name="First",
        )
        second = self.store.add_task(
            routine.routine_id,
            task_type=handler.task_type,
            name="Second",
        )

        result = AutomationService(self.store, self.registry).run_task(
            routine.routine_id,
            second.task_id,
            {"user": "TestViewer"},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual([call[0].task_id for call in handler.calls], [second.task_id])
        self.assertNotEqual(first.task_id, handler.calls[0][0].task_id)
        self.assertEqual(handler.calls[0][1].context["user"], "TestViewer")

    def test_routine_store_round_trip_preserves_named_task_structure(self) -> None:
        routine = self.store.create_managed(
            trigger_id="trigger-1",
            name="Share link",
            managed_by="twitch.command",
            task_type="twitch.send_chat_message",
            task_name="Send Twitch chat response",
            task_config={"message": "Hello"},
        )
        loaded = RoutineStore(self.store.path)
        loaded.load()

        saved = loaded.get(routine.routine_id)
        self.assertEqual(saved.name, "Share link")
        self.assertEqual(saved.tasks[0].name, "Send Twitch chat response")
        self.assertEqual(saved.tasks[0].config["message"], "Hello")

    def test_custom_groups_are_ordered_editable_and_delete_to_ungrouped(self) -> None:
        chat = self.store.add_group("Chat Commands")
        stream = self.store.add_group("Stream Control", collapsed=True)
        routine = self.store.add("Welcome", group_id=chat.group_id)

        self.store.update_group(chat.group_id, name="Chat", collapsed=True)
        self.store.reorder_group(stream.group_id, 0)

        self.assertEqual(
            [group.name for group in self.store.groups],
            ["Stream Control", "Chat"],
        )
        self.assertTrue(self.store.get_group(chat.group_id).collapsed)
        self.assertEqual(self.store.grouped(chat.group_id)[0].routine_id, routine.routine_id)
        self.assertTrue(self.store.delete_group(chat.group_id))
        self.assertEqual(self.store.get(routine.routine_id).group_id, "")
        self.assertEqual(self.store.grouped("")[0].routine_id, routine.routine_id)

    def test_routines_can_move_between_groups_and_reorder(self) -> None:
        first_group = self.store.add_group("First")
        second_group = self.store.add_group("Second")
        first = self.store.add("First routine", group_id=first_group.group_id)
        second = self.store.add("Second routine", group_id=first_group.group_id)
        third = self.store.add("Third routine", group_id=second_group.group_id)

        self.store.move_routine(second.routine_id, first_group.group_id, 0)
        self.assertEqual(
            [routine.routine_id for routine in self.store.grouped(first_group.group_id)],
            [second.routine_id, first.routine_id],
        )

        self.store.move_routine(first.routine_id, second_group.group_id, 0)
        self.assertEqual(
            [routine.routine_id for routine in self.store.grouped(second_group.group_id)],
            [first.routine_id, third.routine_id],
        )

    def test_routine_crud_and_duplicate_detaches_service_relationships(self) -> None:
        group = self.store.add_group("Stream")
        routine = self.store.add(
            "Going Live",
            trigger_id="manual-1",
            group_id=group.group_id,
            description="Prepare the show",
        )
        task = self.store.add_task(
            routine.routine_id,
            task_type="test.example",
            name="First task",
            config={"value": 1},
        )

        changed = self.store.update(
            routine.routine_id,
            name="Start Stream",
            enabled=False,
            description="Updated",
        )
        duplicate = self.store.duplicate(routine.routine_id)

        self.assertEqual(changed.name, "Start Stream")
        self.assertFalse(changed.enabled)
        self.assertEqual(duplicate.group_id, group.group_id)
        self.assertEqual(duplicate.trigger_id, "")
        self.assertEqual(duplicate.managed_by, "")
        self.assertNotEqual(duplicate.tasks[0].task_id, task.task_id)
        self.assertTrue(self.store.delete(duplicate.routine_id))

    def test_task_crud_preserves_order_and_copies_configuration(self) -> None:
        routine = self.store.add("Example")
        first = self.store.add_task(
            routine.routine_id,
            task_type="test.example",
            name="First",
            config={"nested": {"value": 1}},
        )
        second = self.store.add_task(
            routine.routine_id,
            task_type="test.example",
            name="Second",
        )
        self.store.update_task(
            routine.routine_id,
            first.task_id,
            name="Updated",
            enabled=False,
        )
        copied = self.store.duplicate_task(routine.routine_id, first.task_id)
        self.store.move_task(routine.routine_id, second.task_id, 0)

        saved = self.store.get(routine.routine_id)
        self.assertEqual(
            [task.task_id for task in saved.tasks],
            [second.task_id, first.task_id, copied.task_id],
        )
        self.assertEqual(saved.tasks[1].name, "Updated")
        self.assertFalse(saved.tasks[1].enabled)
        self.assertEqual(saved.tasks[2].config, {"nested": {"value": 1}})
        self.assertTrue(self.store.delete_task(routine.routine_id, copied.task_id))

    def test_task_order_can_be_saved_atomically(self) -> None:
        routine = self.store.add("Example")
        first = self.store.add_task(
            routine.routine_id, task_type="test.example", name="First"
        )
        second = self.store.add_task(
            routine.routine_id, task_type="test.example", name="Second"
        )

        self.store.reorder_tasks(routine.routine_id, [second.task_id, first.task_id])

        self.assertEqual(
            [task.task_id for task in self.store.get(routine.routine_id).tasks],
            [second.task_id, first.task_id],
        )
        with self.assertRaisesRegex(ValueError, "every task exactly once"):
            self.store.reorder_tasks(routine.routine_id, [first.task_id])

    def test_managed_update_preserves_extra_tasks_and_primary_task_position(self) -> None:
        routine = self.store.create_managed(
            trigger_id="trigger-1",
            name="Command",
            managed_by="twitch.command",
            task_type="twitch.send_chat_message",
            task_name="Response",
            task_config={"message": "Before"},
        )
        primary_id = routine.tasks[0].task_id
        extra = self.store.add_task(
            routine.routine_id,
            task_type="test.example",
            name="Extra",
            index=0,
        )
        self.store.update_managed_task(
            routine.routine_id,
            name="Updated command",
            managed_by="twitch.command",
            task_type="twitch.send_chat_message",
            task_name="Response",
            task_config={"message": "After"},
        )

        saved = self.store.get(routine.routine_id)
        self.assertEqual([task.task_id for task in saved.tasks], [extra.task_id, primary_id])
        self.assertEqual(saved.tasks[1].config["message"], "After")
        with self.assertRaisesRegex(ValueError, "service-managed task"):
            self.store.delete_task(routine.routine_id, primary_id)
        with self.assertRaisesRegex(ValueError, "through their trigger"):
            self.store.delete(routine.routine_id)

    def test_failed_write_does_not_change_in_memory_editor_state(self) -> None:
        group = self.store.add_group("Original")
        with patch(
            "automation.routines.atomic_write_json",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self.store.update_group(group.group_id, name="Changed")

        self.assertEqual(self.store.get_group(group.group_id).name, "Original")

    def test_version_one_routine_file_migrates_without_losing_tasks(self) -> None:
        self.store.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "routines": [
                        {
                            "routine_id": "legacy-routine",
                            "name": "Legacy",
                            "trigger_id": "legacy-trigger",
                            "tasks": [
                                {
                                    "task_id": "legacy-task",
                                    "task_type": "test.example",
                                    "name": "Legacy task",
                                    "config": {"value": 1},
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        self.store.load()

        saved = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 3)
        self.assertEqual(saved["groups"], [])
        self.assertEqual(saved["routines"][0]["routine_id"], "legacy-routine")
        self.assertEqual(saved["routines"][0]["tasks"][0]["task_id"], "legacy-task")


class SendTwitchChatMessageTaskTests(unittest.TestCase):
    def test_task_renders_trigger_context_and_uses_bot_account(self) -> None:
        twitch_service = Mock()
        twitch_service.send_message.return_value = True
        handler = SendTwitchChatMessageTask(twitch_service)
        task = TaskDefinition(
            task_id="task-1",
            task_type=handler.task_type,
            name="Send response",
            config={"message": "Hello {user} in {channel}", "as_bot": True},
        )

        result = handler.execute(
            task,
            TriggerEvent(
                trigger_id="trigger-1",
                service="twitch",
                trigger_type="command",
                context={"user": "Viewer", "channel": "sally"},
            ),
        )

        self.assertTrue(result.succeeded)
        twitch_service.send_message.assert_called_once_with(
            "Hello Viewer in sally",
            as_bot=True,
        )


if __name__ == "__main__":
    unittest.main()
