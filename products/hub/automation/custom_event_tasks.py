from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from products.hub.automation.cancellation import current_cancellation
from products.hub.automation.core_triggers import CoreTriggerStore, normalize_custom_event_name
from products.hub.automation.models import (
    AutomationExecutionResult, TaskDefinition, TaskExecutionResult, TriggerEvent,
)
from products.hub.automation.service import AutomationService, active_routine
from products.hub.automation.variable_registry import VariableRegistry, placeholder_names
from shared.streamhouse_runtime.logger import Logger


class CustomEventDispatcher(QObject):
    """Only a queued Qt delivery may enter the normal Automation publisher."""

    _pending = Signal(object)

    def __init__(
        self, store: CoreTriggerStore, automation: AutomationService,
        record: Callable[[AutomationExecutionResult, str], None], parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.automation = automation
        self.record = record
        self._open = True
        self._pending.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def post(self, event: TriggerEvent) -> None:
        if not self._open:
            raise ValueError("Custom Events are unavailable during shutdown.")
        self._pending.emit(event)

    def stop(self) -> None:
        self._open = False

    @Slot(object)
    def _deliver(self, event: TriggerEvent) -> None:
        if not self._open:
            return
        name = normalize_custom_event_name(event.context.get("core.custom_event.name", ""))
        # Resolve matches at delivery; recheck state in case a prior listener
        # in this same dispatch disabled a later trigger.
        for trigger in self.store.evaluate_custom_event(event):
            target = self.store.get(trigger.trigger_id)
            if (
                target is None or not target.enabled or target.event_type != "custom_event"
                or target.event_name.casefold() != name.casefold()
            ):
                continue
            execution = self.automation.publish_trigger(trigger)
            if execution.handled:
                self.record(execution, "Custom Event")


class SendCustomEventTask:
    task_type = "core.send_custom_event"

    def __init__(self, variables: VariableRegistry, dispatcher: CustomEventDispatcher) -> None:
        self.variables = variables
        self.dispatcher = dispatcher

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            name = normalize_custom_event_name(self.variables.render(
                str(task.config.get("event_name", "")), trigger.context
            ))
            if not name or placeholder_names(name):
                raise ValueError("Enter a nonempty Event Name with no unresolved placeholders.")
            routine = active_routine()
            execution = current_cancellation()
            ancestry = tuple(dict.fromkeys((
                *trigger.routine_ancestry,
                *(execution.routine_stack if execution else ()),
                *((routine.routine_id,) if routine else ()),
            )))
            if len(ancestry) >= self.dispatcher.automation.max_routine_depth:
                raise ValueError("Custom Event chain reached the routine nesting limit.")
            self.dispatcher.post(TriggerEvent(
                trigger_id="core.custom_event",
                service="core",
                trigger_type="custom_event",
                context={
                    "core.custom_event.name": name,
                    "core.custom_event.data": self.variables.render(
                        str(task.config.get("event_data", "")), trigger.context
                    ),
                    "core.custom_event.source_routine": routine.name if routine else "",
                },
                routine_ancestry=ancestry,
            ))
        except ValueError as error:
            return TaskExecutionResult(task.task_id, task.task_type, False, str(error))
        return TaskExecutionResult(task.task_id, task.task_type, True, "Custom Event queued for delivery.")


class LogMessageTask:
    task_type = "core.log_message"
    LEVELS = {
        "debug": Logger.debug, "info": Logger.info,
        "warning": Logger.warning, "error": Logger.error,
    }

    def __init__(self, variables: VariableRegistry) -> None:
        self.variables = variables

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        level = str(task.config.get("level", "info")).strip().casefold()
        log = self.LEVELS.get(level)
        if log is None:
            return TaskExecutionResult(task.task_id, task.task_type, False, "Choose a valid log level.")
        routine = active_routine()
        message = self.variables.render(str(task.config.get("message", "")), trigger.context)
        log(f"{routine.name if routine else 'Routine'}: {message}", source="AUTOMATION")
        return TaskExecutionResult(task.task_id, task.task_type, True, f"Logged {level.title()} message.")
