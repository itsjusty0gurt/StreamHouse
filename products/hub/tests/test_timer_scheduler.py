import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.timer_scheduler import AutomationTimerScheduler


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class TimerHarness:
    def __init__(self, choices=None) -> None:
        _app()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.routines = RoutineStore(root / "routines.json")
        self.store = CoreTriggerStore(root / "core.json", self.routines)
        self.now = 0.0
        self.events = []
        self.choices = iter(choices or ())
        self.scheduler = AutomationTimerScheduler(
            self.store,
            lambda event, description: self.events.append((event, description)),
            clock=lambda: self.now,
            choose_delay=lambda _low, _high: next(self.choices),
            auto_arm=False,
        )
        self.scheduler.start()

    def close(self) -> None:
        self.scheduler.shutdown()
        self.temporary.cleanup()


def test_fixed_timer_fires_repeats_and_starts_fresh() -> None:
    harness = TimerHarness()
    try:
        routine = harness.routines.add("Promo")
        trigger = harness.store.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="10",
            timer_minimum_unit="seconds",
        )
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 10

        harness.now = 9
        harness.scheduler.process_due()
        assert harness.events == []
        harness.now = 10
        harness.scheduler.process_due()
        assert len(harness.events) == 1
        assert harness.events[0][0].trigger_type == "timer"
        assert harness.events[0][1] == "Fixed 10 seconds"
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 10

        harness.now = 20
        harness.scheduler.process_due()
        assert len(harness.events) == 2
    finally:
        harness.close()


def test_random_timer_resamples_after_every_firing() -> None:
    harness = TimerHarness([30, 60, 45])
    try:
        routine = harness.routines.add("Random sound")
        trigger = harness.store.add_timer(
            routine.routine_id,
            timer_mode="random",
            timer_minimum="30",
            timer_minimum_unit="seconds",
            timer_maximum="60",
            timer_maximum_unit="seconds",
        )
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 30
        harness.now = 30
        harness.scheduler.process_due()
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 60
        harness.now = 90
        harness.scheduler.process_due()
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 45
        assert len(harness.events) == 2
    finally:
        harness.close()


def test_late_processing_does_not_catch_up_missed_intervals() -> None:
    harness = TimerHarness()
    try:
        routine = harness.routines.add("No catch-up")
        trigger = harness.store.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="10",
            timer_minimum_unit="seconds",
        )
        harness.now = 100
        harness.scheduler.process_due()

        assert len(harness.events) == 1
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 10
    finally:
        harness.close()


def test_equal_random_bounds_work_without_sampling() -> None:
    harness = TimerHarness()
    try:
        routine = harness.routines.add("Equal range")
        trigger = harness.store.add_timer(
            routine.routine_id,
            timer_mode="random",
            timer_minimum="1.5",
            timer_minimum_unit="minutes",
            timer_maximum="90",
            timer_maximum_unit="seconds",
        )
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 90
    finally:
        harness.close()


def test_edit_disable_reenable_delete_and_shutdown_cancel_schedules() -> None:
    harness = TimerHarness()
    try:
        routine = harness.routines.add("Lifecycle")
        trigger = harness.store.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="10",
            timer_minimum_unit="seconds",
        )
        harness.now = 2
        harness.store.update_timer(
            trigger.trigger_id,
            timer_mode="fixed",
            timer_minimum="20",
            timer_minimum_unit="seconds",
        )
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 20
        harness.store.update_timer(
            trigger.trigger_id,
            timer_mode="fixed",
            timer_minimum="20",
            timer_minimum_unit="seconds",
            enabled=False,
        )
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) is None
        harness.now = 5
        harness.store.update_timer(
            trigger.trigger_id,
            timer_mode="fixed",
            timer_minimum="20",
            timer_minimum_unit="seconds",
            enabled=True,
        )
        assert harness.scheduler.next_delay_seconds(trigger.trigger_id) == 20
        harness.store.delete(trigger.trigger_id)
        harness.now = 100
        harness.scheduler.process_due()
        assert harness.events == []
        assert harness.scheduler.schedules == {}
        harness.scheduler.shutdown()
        assert not harness.scheduler.timer.isActive()
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("mode", "minimum", "minimum_unit", "maximum", "maximum_unit"),
    (
        ("fixed", "", "seconds", "", "seconds"),
        ("fixed", "0", "seconds", "", "seconds"),
        ("fixed", "-1", "seconds", "", "seconds"),
        ("fixed", "invalid", "seconds", "", "seconds"),
        ("fixed", "NaN", "seconds", "", "seconds"),
        ("fixed", "Infinity", "seconds", "", "seconds"),
        ("fixed", "1e999", "seconds", "", "seconds"),
        ("random", "60", "seconds", "30", "seconds"),
    ),
)
def test_invalid_timer_values_are_rejected(
    mode, minimum, minimum_unit, maximum, maximum_unit
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        routines = RoutineStore(Path(temporary) / "routines.json")
        store = CoreTriggerStore(Path(temporary) / "core.json", routines)
        routine = routines.add("Invalid")
        with pytest.raises(ValueError):
            store.add_timer(
                routine.routine_id,
                timer_mode=mode,
                timer_minimum=minimum,
                timer_minimum_unit=minimum_unit,
                timer_maximum=maximum,
                timer_maximum_unit=maximum_unit,
            )


def test_timer_firing_uses_routine_custom_queue() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        routines = RoutineStore(root / "routines.json")
        queues = AutomationQueueStore(root / "queues.json")
        custom_queue = queues.add("Promos")
        manager = AutomationQueueManager(queues)
        service = AutomationService(routines, TaskRegistry(), queue_manager=manager)
        store = CoreTriggerStore(root / "core.json", routines)
        routine = routines.add("Promo", queue_id=custom_queue.queue_id)
        trigger = store.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="1",
            timer_minimum_unit="seconds",
        )
        results = []
        scheduler = AutomationTimerScheduler(
            store,
            lambda event, _description: results.append(service.publish_trigger(event)),
            clock=lambda: 0,
            auto_arm=False,
        )
        scheduler.start()
        scheduler.process_due(1)

        assert results[0].trigger_id == trigger.trigger_id
        assert results[0].routine_results[0].queue_id == custom_queue.queue_id
        scheduler.shutdown()
