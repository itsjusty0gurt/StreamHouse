from __future__ import annotations

from dataclasses import replace
from time import perf_counter

from products.hub.automation.models import (
    AutomationExecutionResult,
    RoutineExecutionResult,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.queues import AutomationQueueManager, QueuedRoutine
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.core.events import Events
from shared.streamhouse_runtime.logger import Logger


class AutomationService:
    """Publish service triggers and execute their registered routines."""

    def __init__(
        self,
        routine_store: RoutineStore,
        task_registry: TaskRegistry,
        variable_store: CustomVariableStore | None = None,
        queue_manager: AutomationQueueManager | None = None,
        variable_registry: VariableRegistry | None = None,
    ) -> None:
        self.routine_store = routine_store
        self.task_registry = task_registry
        self.variable_store = variable_store or CustomVariableStore()
        self.queue_manager = queue_manager
        self.variable_registry = variable_registry
        self._routine_stack: list[str] = []
        self.max_routine_depth = 10

    def publish_trigger(self, trigger: TriggerEvent) -> AutomationExecutionResult:
        source_trigger = trigger
        trigger = self._prepare_trigger(source_trigger)
        Events.emit("trigger_fired", trigger=trigger)
        Events.emit(
            f"trigger_fired.{trigger.service}.{trigger.trigger_type}",
            trigger=trigger,
        )
        results: list[RoutineExecutionResult] = []
        for routine in self.routine_store.matching(trigger.trigger_id):
            prepared = self._prepare_trigger(source_trigger)
            routine_result = self._publish_routine(routine, prepared)
            results.append(routine_result)
        return AutomationExecutionResult(
            event_id=trigger.event_id,
            trigger_id=trigger.trigger_id,
            routine_results=tuple(results),
        )

    def _publish_routine(self, routine, trigger: TriggerEvent) -> RoutineExecutionResult:
        if not routine.queue_id or self.queue_manager is None:
            return self._execute_routine(routine, trigger)
        queue_was_busy = (
            self.queue_manager.count(routine.queue_id) > 0
            or routine.queue_id in self.queue_manager.current
        )
        queued = self.queue_manager.enqueue(
            routine.queue_id,
            routine.routine_id,
            routine.name,
            trigger,
        )
        Events.emit(
            "automation_queue_changed",
            queue_id=routine.queue_id,
        )
        if not queued.accepted:
            ignored = queued.detail.startswith("Ignored duplicate")
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=ignored,
                detail=queued.detail,
            )
        ready = (
            None
            if queue_was_busy
            else self.queue_manager.take_ready(routine.queue_id)
        )
        if ready is None:
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=True,
                detail=queued.detail,
            )
        result = self._execute_queued_item(ready)
        return result

    def _execute_queued_item(self, item: QueuedRoutine) -> RoutineExecutionResult:
        try:
            routine = self.routine_store.get(item.routine_id)
            if routine is None:
                return RoutineExecutionResult(
                    routine_id=item.routine_id,
                    succeeded=False,
                    detail="The queued routine no longer exists.",
                )
            if not routine.enabled:
                return RoutineExecutionResult(
                    routine_id=item.routine_id,
                    succeeded=False,
                    detail=f'Queued routine "{routine.name}" is disabled.',
                )
            Events.emit("automation_queue_item_started", item=item)
            return self._execute_routine(routine, item.trigger)
        finally:
            if self.queue_manager is not None:
                self.queue_manager.complete(item.queue_id)
                Events.emit("automation_queue_changed", queue_id=item.queue_id)

    def process_queues(self) -> tuple[AutomationExecutionResult, ...]:
        """Run at most one ready item per queue.

        The UI calls this periodically on Qt's main thread so Qt-based tasks
        remain thread-safe while paused and delayed queues can build a backlog.
        """
        if self.queue_manager is None:
            return ()
        executions: list[AutomationExecutionResult] = []
        for queue in tuple(self.queue_manager.store.queues):
            item = self.queue_manager.take_ready(queue.queue_id)
            if item is None:
                continue
            result = self._execute_queued_item(item)
            execution = AutomationExecutionResult(
                event_id=item.trigger.event_id,
                trigger_id=item.trigger.trigger_id,
                routine_results=(result,),
            )
            executions.append(execution)
            Events.emit(
                "automation_queue_execution_completed",
                execution=execution,
                item=item,
            )
        return tuple(executions)

    def run_routine(
        self,
        routine_id: str,
        context: dict[str, str] | None = None,
    ) -> AutomationExecutionResult:
        routine = self.routine_store.get(routine_id)
        if routine is None:
            raise ValueError("The selected routine no longer exists.")
        trigger = TriggerEvent(
            trigger_id=routine.trigger_id or f"manual.{routine.routine_id}",
            service="streamhouse",
            trigger_type="manual",
            context=context or {},
        )
        trigger = self._prepare_trigger(trigger)
        Events.emit("trigger_fired", trigger=trigger)
        Events.emit("trigger_fired.streamhouse.manual", trigger=trigger)
        result = self._execute_routine(routine, trigger)
        return AutomationExecutionResult(
            event_id=trigger.event_id,
            trigger_id=trigger.trigger_id,
            routine_results=(result,),
        )

    def run_task(
        self,
        routine_id: str,
        task_id: str,
        context: dict[str, str] | None = None,
    ) -> AutomationExecutionResult:
        routine = self.routine_store.get(routine_id)
        if routine is None:
            raise ValueError("The selected routine no longer exists.")
        task = next((value for value in routine.tasks if value.task_id == task_id), None)
        if task is None:
            raise ValueError("The selected task no longer exists.")
        trigger = TriggerEvent(
            trigger_id=f"manual.task.{task.task_id}",
            service="streamhouse",
            trigger_type="task_test",
            context=context or {},
        )
        trigger = self._prepare_trigger(trigger)
        Events.emit("trigger_fired", trigger=trigger)
        Events.emit("trigger_fired.streamhouse.task_test", trigger=trigger)
        task_result = self._execute_task(routine, task, trigger)
        routine_result = RoutineExecutionResult(
            routine_id=routine.routine_id,
            succeeded=task_result.succeeded,
            task_results=(task_result,),
            detail="" if task_result.succeeded else "Selected task test failed.",
        )
        return AutomationExecutionResult(
            event_id=trigger.event_id,
            trigger_id=trigger.trigger_id,
            routine_results=(routine_result,),
        )

    def run_nested_routine(
        self,
        routine_id: str,
        trigger: TriggerEvent,
    ) -> RoutineExecutionResult:
        routine = self.routine_store.get(routine_id)
        if routine is None:
            return RoutineExecutionResult(
                routine_id=routine_id,
                succeeded=False,
                detail="The selected nested routine no longer exists.",
            )
        if not routine.enabled:
            return RoutineExecutionResult(
                routine_id=routine_id,
                succeeded=False,
                detail=f'Nested routine "{routine.name}" is disabled.',
            )
        return self._execute_routine(routine, trigger)

    def routine_name(self, routine_id: str) -> str:
        routine = self.routine_store.get(routine_id)
        return routine.name if routine is not None else ""

    def _prepare_trigger(self, trigger: TriggerEvent) -> TriggerEvent:
        context = {str(key): str(value) for key, value in trigger.context.items()}
        if self.variable_registry is not None:
            context.update(self.variable_registry.context_values(context))
        return replace(trigger, context=context)

    def _execute_routine(self, routine, trigger: TriggerEvent) -> RoutineExecutionResult:
        if routine.routine_id in self._routine_stack:
            chain = " -> ".join((*self._routine_stack, routine.routine_id))
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=False,
                detail=f"Routine call loop blocked: {chain}.",
            )
        if len(self._routine_stack) >= self.max_routine_depth:
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=False,
                detail=f"Routine nesting is limited to {self.max_routine_depth} levels.",
            )
        self._routine_stack.append(routine.routine_id)
        Events.emit("routine_started", trigger=trigger, routine=routine)
        try:
            task_results = []
            flow_action = ""
            for task in routine.tasks:
                if not task.enabled:
                    continue
                self._refresh_registry_values(trigger)
                task_result = self._execute_task(routine, task, trigger)
                task_results.append(task_result)
                if not task_result.succeeded:
                    break
                if task_result.flow_action:
                    flow_action = task_result.flow_action
                    break
            succeeded = bool(task_results) and all(
                result.succeeded for result in task_results
            )
            routine_result = RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=succeeded,
                task_results=tuple(task_results),
                detail=(
                    "Routine stopped by logic."
                    if succeeded and flow_action == "break"
                    else "" if succeeded else "Routine stopped after a failed task."
                ),
                flow_action=flow_action,
            )
            Events.emit(
                "routine_completed" if succeeded else "routine_failed",
                trigger=trigger,
                routine=routine,
                result=routine_result,
            )
            return routine_result
        finally:
            self._routine_stack.pop()

    def _refresh_registry_values(self, trigger: TriggerEvent) -> None:
        """Refresh global/contextual snapshots before each routine task.

        Domain tasks can mutate state that a later task reads through a normal
        registry variable. Refreshing here keeps composed routines on the same
        authoritative provider path without task-specific output variables.
        """
        if self.variable_registry is not None and isinstance(trigger.context, dict):
            trigger.context.update(
                self.variable_registry.context_values(trigger.context)
            )

    def _execute_task(self, routine, task, trigger: TriggerEvent) -> TaskExecutionResult:
        Events.emit(
            "task_started",
            trigger=trigger,
            routine=routine,
            task=task,
        )
        started_at = perf_counter()
        try:
            task_result = self.task_registry.execute(task, trigger)
        except Exception as error:
            Logger.exception(
                f'Task "{task.name}" failed unexpectedly.',
                source="AUTOMATION",
            )
            task_result = TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail=str(error),
            )
        task_result = replace(
            task_result,
            duration_ms=max(int((perf_counter() - started_at) * 1000), 0),
        )
        Events.emit(
            "task_completed" if task_result.succeeded else "task_failed",
            trigger=trigger,
            routine=routine,
            task=task,
            result=task_result,
        )
        return task_result
