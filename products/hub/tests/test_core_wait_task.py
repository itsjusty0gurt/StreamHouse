from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import WaitTask
from products.hub.automation.models import (
    DEFAULT_AUTOMATION_QUEUE_ID,
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.task_catalog import BUILTIN_TASK_METADATA
from products.hub.automation.tasks import TaskRegistry


class MarkerTask:
    task_type = "test.marker"

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def execute(self, task, trigger):
        self.events.append(task.name)
        return TaskExecutionResult(task.task_id, task.task_type, True, "Marked.")


class CoreWaitTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def task(duration: object, unit: str = "seconds") -> TaskDefinition:
        return TaskDefinition(
            "wait",
            "core.wait",
            "Wait",
            {"duration": duration, "unit": unit},
        )

    @staticmethod
    def trigger(**context: str) -> TriggerEvent:
        return TriggerEvent("manual", "test", "manual", context)

    def test_supported_units_and_decimal_values_normalize_to_milliseconds(self) -> None:
        cases = (
            ("250", "milliseconds", 250),
            ("1.5", "seconds", 1500),
            ("1.25", "minutes", 75_000),
            ("0", "seconds", 0),
        )
        for duration, unit, expected in cases:
            waited: list[int] = []
            with self.subTest(duration=duration, unit=unit):
                result = WaitTask(
                    wait=lambda milliseconds: waited.append(milliseconds) or True
                ).execute(self.task(duration, unit), self.trigger())
                self.assertTrue(result.succeeded)
                self.assertEqual(waited, [expected])

    def test_canonical_variable_duration_resolves_from_routine_context(self) -> None:
        waited: list[int] = []
        result = WaitTask(
            wait=lambda milliseconds: waited.append(milliseconds) or True
        ).execute(
            self.task("{custom.overlay_delay}"),
            self.trigger(**{"custom.overlay_delay": "2.5"}),
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(waited, [2500])

    def test_invalid_duration_fails_without_waiting(self) -> None:
        invalid = ("", "banana", "-1", "nan", "inf", "{custom.missing}")
        for value in invalid:
            calls: list[int] = []
            with self.subTest(value=value):
                result = WaitTask(
                    wait=lambda milliseconds: calls.append(milliseconds) or True
                ).execute(self.task(value), self.trigger())
                self.assertFalse(result.succeeded)
                self.assertEqual(calls, [])

    def test_nested_qt_event_loop_keeps_ui_events_processing(self) -> None:
        observed: list[str] = []
        QTimer.singleShot(10, lambda: observed.append("ui-event"))

        started = time.perf_counter()
        result = WaitTask().execute(
            self.task("40", "milliseconds"),
            self.trigger(),
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(observed, ["ui-event"])
        self.assertGreaterEqual(time.perf_counter() - started, 0.02)

    def test_shutdown_cancels_long_wait(self) -> None:
        wait = WaitTask()
        QTimer.singleShot(20, wait.cancel_all)

        started = time.perf_counter()
        result = wait.execute(self.task("10", "seconds"), self.trigger())

        self.assertFalse(result.succeeded)
        self.assertIn("cancelled", result.detail.casefold())
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_queue_cancellation_interrupts_wait_and_skips_remaining_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RoutineStore(root / "routines.json")
            queue_manager = AutomationQueueManager(
                AutomationQueueStore(root / "queues.json")
            )
            routine = store.add("Long wait")
            store.add_task(
                routine.routine_id,
                task_type="core.wait",
                name="Wait",
                config={"duration": "10", "unit": "seconds"},
            )
            store.add_task(
                routine.routine_id,
                task_type="test.marker",
                name="Must not run",
            )
            events: list[str] = []
            registry = TaskRegistry()
            registry.register(WaitTask())
            registry.register(MarkerTask(events))
            service = AutomationService(
                store,
                registry,
                queue_manager=queue_manager,
            )
            QTimer.singleShot(
                20,
                lambda: queue_manager.cancel_current(
                    DEFAULT_AUTOMATION_QUEUE_ID
                ),
            )

            started = time.perf_counter()
            result = service.run_routine(routine.routine_id).routine_results[0]

        self.assertTrue(result.cancelled)
        self.assertFalse(result.succeeded)
        self.assertTrue(result.task_results[0].cancelled)
        self.assertEqual(events, [])
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_routine_continues_in_order_after_wait(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Overlay sequence")
            store.add_task(
                routine.routine_id,
                task_type="test.marker",
                name="show",
            )
            store.add_task(
                routine.routine_id,
                task_type="core.wait",
                name="wait",
                config={"duration": "20", "unit": "milliseconds"},
            )
            store.add_task(
                routine.routine_id,
                task_type="test.marker",
                name="hide",
            )
            events: list[str] = []
            registry = TaskRegistry()
            registry.register(MarkerTask(events))
            registry.register(
                WaitTask(wait=lambda _milliseconds: events.append("wait") or True)
            )

            result = AutomationService(store, registry).run_routine(routine.routine_id)

            self.assertTrue(result.succeeded)
            self.assertEqual(events, ["show", "wait", "hide"])
            self.assertEqual(store.get(routine.routine_id).queue_id, routine.queue_id)

    def test_task_library_registers_wait_under_core(self) -> None:
        registry = TaskRegistry(BUILTIN_TASK_METADATA)
        metadata = registry.metadata("core.wait")

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.category, "Core")
        self.assertEqual(metadata.label, "Wait")
        self.assertIn("Hub stays responsive", metadata.short_description)
        self.assertIsNone(registry.metadata("core.delay"))


if __name__ == "__main__":
    unittest.main()
