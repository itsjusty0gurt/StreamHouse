from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from random import uniform
from time import monotonic
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Slot

from products.hub.automation.core_triggers import (
    CoreAutomationTrigger,
    CoreTriggerStore,
)
from products.hub.automation.models import TriggerEvent


@dataclass(frozen=True, slots=True)
class ScheduledTimer:
    deadline: float
    fingerprint: tuple[str, ...]


class AutomationTimerScheduler(QObject):
    """One Qt scheduler for every persisted Automation Timer trigger."""

    MAX_QT_INTERVAL_MS = 2_147_000_000

    def __init__(
        self,
        store: CoreTriggerStore,
        fired: Callable[[TriggerEvent, str], None],
        *,
        clock: Callable[[], float] = monotonic,
        choose_delay: Callable[[float, float], float] = uniform,
        auto_arm: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.fired = fired
        self.clock = clock
        self.choose_delay = choose_delay
        self.auto_arm = auto_arm
        self.schedules: dict[str, ScheduledTimer] = {}
        self.running = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.process_due)
        self.store.subscribe_changes(self.synchronize)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.synchronize()

    def shutdown(self) -> None:
        self.running = False
        self.timer.stop()
        self.schedules.clear()
        self.store.unsubscribe_changes(self.synchronize)

    def synchronize(self) -> None:
        if not self.running:
            return
        active = {
            trigger.trigger_id: trigger
            for trigger in self.store.triggers
            if trigger.enabled and trigger.event_type == "timer"
        }
        for trigger_id in tuple(self.schedules):
            if trigger_id not in active:
                self.schedules.pop(trigger_id, None)
        now = self.clock()
        for trigger_id, trigger in active.items():
            fingerprint = self._fingerprint(trigger)
            existing = self.schedules.get(trigger_id)
            if existing is None or existing.fingerprint != fingerprint:
                self._schedule(trigger, now)
        self._arm()

    @Slot()
    def process_due(self, now: float | None = None) -> None:
        if not self.running:
            return
        current = self.clock() if now is None else float(now)
        due = sorted(
            (
                (trigger_id, schedule)
                for trigger_id, schedule in self.schedules.items()
                if schedule.deadline <= current
            ),
            key=lambda item: item[1].deadline,
        )
        for trigger_id, _schedule in due:
            self.schedules.pop(trigger_id, None)
            trigger = self.store.get(trigger_id)
            event = self.store.event_for(trigger_id)
            if trigger is None or event is None:
                continue
            description = self.store.timer_description(trigger)
            fingerprint = self._fingerprint(trigger)
            self._schedule(trigger, current)
            if self.auto_arm:
                QTimer.singleShot(
                    0,
                    lambda trigger_id=trigger_id, value=event, label=description, expected=fingerprint: (
                        self._deliver(trigger_id, value, label, expected)
                    ),
                )
            else:
                self._deliver(trigger_id, event, description, fingerprint)
        self._arm()

    def _deliver(
        self,
        trigger_id: str,
        event: TriggerEvent,
        description: str,
        expected: tuple[str, ...],
    ) -> None:
        trigger = self.store.get(trigger_id)
        if (
            not self.running
            or trigger is None
            or self.store.event_for(trigger_id) is None
            or self._fingerprint(trigger) != expected
        ):
            return
        self.fired(event, description)

    def next_delay_seconds(self, trigger_id: str) -> float | None:
        schedule = self.schedules.get(trigger_id)
        if schedule is None:
            return None
        return max(schedule.deadline - self.clock(), 0.0)

    def _schedule(self, trigger: CoreAutomationTrigger, now: float) -> None:
        minimum, maximum = self.store.timer_bounds_seconds(trigger)
        delay = minimum if minimum == maximum else self.choose_delay(minimum, maximum)
        delay = min(max(float(delay), minimum), maximum)
        self.schedules[trigger.trigger_id] = ScheduledTimer(
            deadline=now + delay,
            fingerprint=self._fingerprint(trigger),
        )

    def _arm(self) -> None:
        self.timer.stop()
        if not self.auto_arm or not self.running or not self.schedules:
            return
        remaining = min(item.deadline for item in self.schedules.values()) - self.clock()
        milliseconds = min(
            max(ceil(remaining * 1000), 1),
            self.MAX_QT_INTERVAL_MS,
        )
        self.timer.start(milliseconds)

    @staticmethod
    def _fingerprint(trigger: CoreAutomationTrigger) -> tuple[str, ...]:
        return (
            trigger.timer_mode,
            trigger.timer_minimum,
            trigger.timer_minimum_unit,
            trigger.timer_maximum,
            trigger.timer_maximum_unit,
        )
