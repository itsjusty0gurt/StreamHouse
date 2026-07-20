from __future__ import annotations

from dataclasses import replace
from time import perf_counter

from automation.models import (
    AutomationExecutionResult,
    RoutineExecutionResult,
    TaskExecutionResult,
    TriggerEvent,
)
from automation.routines import RoutineStore
from automation.tasks import TaskRegistry
from core.events import Events
from core.logger import Logger


class AutomationService:
    """Publish service triggers and execute their registered routines."""

    def __init__(
        self,
        routine_store: RoutineStore,
        task_registry: TaskRegistry,
    ) -> None:
        self.routine_store = routine_store
        self.task_registry = task_registry

    def publish_trigger(self, trigger: TriggerEvent) -> AutomationExecutionResult:
        Events.emit("trigger_fired", trigger=trigger)
        Events.emit(
            f"trigger_fired.{trigger.service}.{trigger.trigger_type}",
            trigger=trigger,
        )
        results: list[RoutineExecutionResult] = []
        for routine in self.routine_store.matching(trigger.trigger_id):
            routine_result = self._execute_routine(routine, trigger)
            results.append(routine_result)
        return AutomationExecutionResult(
            event_id=trigger.event_id,
            trigger_id=trigger.trigger_id,
            routine_results=tuple(results),
        )

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
            service="sally",
            trigger_type="manual",
            context=context or {},
        )
        Events.emit("trigger_fired", trigger=trigger)
        Events.emit("trigger_fired.sally.manual", trigger=trigger)
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
            service="sally",
            trigger_type="task_test",
            context=context or {},
        )
        Events.emit("trigger_fired", trigger=trigger)
        Events.emit("trigger_fired.sally.task_test", trigger=trigger)
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

    def _execute_routine(self, routine, trigger: TriggerEvent) -> RoutineExecutionResult:
        Events.emit("routine_started", trigger=trigger, routine=routine)
        task_results = []
        for task in routine.tasks:
            if not task.enabled:
                continue
            task_result = self._execute_task(routine, task, trigger)
            task_results.append(task_result)
            if not task_result.succeeded:
                break
        succeeded = bool(task_results) and all(
            result.succeeded for result in task_results
        )
        routine_result = RoutineExecutionResult(
            routine_id=routine.routine_id,
            succeeded=succeeded,
            task_results=tuple(task_results),
            detail="" if succeeded else "Routine stopped after a failed task.",
        )
        Events.emit(
            "routine_completed" if succeeded else "routine_failed",
            trigger=trigger,
            routine=routine,
            result=routine_result,
        )
        return routine_result

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
