from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter

from products.hub.automation.cancellation import (
    AutomationCancellation,
    cancellation_scope,
    current_cancellation,
)
from products.hub.automation.models import (
    END_ROUTINE_ACTION,
    AutomationExecutionResult,
    RoutineDefinition,
    RoutineExecutionResult,
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.queues import (
    AutomationQueueManager,
    AutomationQueueStore,
    QueuedRoutine,
)
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.core.events import Events
from shared.streamhouse_runtime.logger import Logger


_active_routine: ContextVar[RoutineDefinition | None] = ContextVar(
    "automation_active_routine",
    default=None,
)


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
        self.queue_manager = queue_manager or AutomationQueueManager(
            AutomationQueueStore()
        )
        self.variable_registry = variable_registry
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
        queue = self.queue_manager.store.resolve(routine.queue_id)
        queued, ready = self.queue_manager.submit(
            queue.queue_id,
            routine.routine_id,
            routine.name,
            trigger,
        )
        Events.emit(
            "automation_queue_changed",
            queue_id=queue.queue_id,
        )
        if not queued.accepted:
            ignored = queued.detail.startswith("Ignored duplicate")
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=ignored,
                detail=queued.detail,
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
            return self._execute_routine(
                routine,
                item.trigger,
                self.queue_manager.cancellation_for(item),
            )
        finally:
            self.queue_manager.complete(item.queue_id)
            Events.emit("automation_queue_changed", queue_id=item.queue_id)

    def process_queues(self) -> tuple[AutomationExecutionResult, ...]:
        """Run at most one ready item per queue.

        The UI calls this periodically on Qt's main thread so Qt-based tasks
        remain thread-safe while paused and delayed queues can build a backlog.
        """
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
        result = self._publish_routine(routine, trigger)
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
        started_at = datetime.now(timezone.utc)
        started_clock = perf_counter()
        execution = AutomationCancellation(queue_id=routine.queue_id)
        with cancellation_scope(execution):
            routine_token = _active_routine.set(routine)
            try:
                task_result = self._execute_task(routine, task, trigger)
            finally:
                _active_routine.reset(routine_token)
        routine_result = RoutineExecutionResult(
            routine_id=routine.routine_id,
            succeeded=task_result.succeeded,
            task_results=(task_result,),
            detail="" if task_result.succeeded else "Selected task test failed.",
            queue_id=routine.queue_id,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=max(int((perf_counter() - started_clock) * 1000), 0),
            trigger_service=trigger.service,
            trigger_type=trigger.trigger_type,
            trigger_occurred_at=trigger.occurred_at,
            context_values=self.safe_execution_context(trigger.context),
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

    def _execute_routine(
        self,
        routine,
        trigger: TriggerEvent,
        cancellation: AutomationCancellation | None = None,
    ) -> RoutineExecutionResult:
        execution = cancellation or current_cancellation() or AutomationCancellation()
        started_at = datetime.now(timezone.utc)
        started_clock = perf_counter()
        routine_stack = execution.routine_stack
        if routine.routine_id in routine_stack:
            chain = " -> ".join((*routine_stack, routine.routine_id))
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=False,
                detail=f"Routine call loop blocked: {chain}.",
            )
        if len(routine_stack) >= self.max_routine_depth:
            return RoutineExecutionResult(
                routine_id=routine.routine_id,
                succeeded=False,
                detail=f"Routine nesting is limited to {self.max_routine_depth} levels.",
            )
        with cancellation_scope(execution):
            routine_stack.append(routine.routine_id)
            routine_token = _active_routine.set(routine)
            Events.emit("routine_started", trigger=trigger, routine=routine)
            try:
                task_results, flow_action = self._execute_task_sequence(
                    routine,
                    tuple(routine.tasks),
                    trigger,
                    execution,
                )
                cancelled = execution.cancelled
                succeeded = (
                    not cancelled
                    and bool(task_results)
                    and all(result.succeeded for result in task_results)
                )
                routine_result = RoutineExecutionResult(
                    routine_id=routine.routine_id,
                    succeeded=succeeded,
                    task_results=tuple(task_results),
                    detail=(
                        execution.reason
                        if cancelled
                        else "Routine completed early because End Routine was reached."
                        if succeeded and flow_action == END_ROUTINE_ACTION
                        else ""
                        if succeeded
                        else "Routine stopped after a failed task."
                    ),
                    flow_action=flow_action,
                    cancelled=cancelled,
                    queue_id=execution.queue_id or routine.queue_id,
                    started_at=started_at.isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=max(int((perf_counter() - started_clock) * 1000), 0),
                    trigger_service=trigger.service,
                    trigger_type=trigger.trigger_type,
                    trigger_occurred_at=trigger.occurred_at,
                    context_values=self.safe_execution_context(trigger.context),
                )
                Events.emit(
                    "routine_cancelled"
                    if cancelled
                    else "routine_completed"
                    if succeeded
                    else "routine_failed",
                    trigger=trigger,
                    routine=routine,
                    result=routine_result,
                )
                return routine_result
            finally:
                _active_routine.reset(routine_token)
                routine_stack.pop()

    def execute_child_tasks(
        self,
        tasks: tuple[TaskDefinition, ...],
        trigger: TriggerEvent,
    ) -> tuple[TaskExecutionResult, ...]:
        """Execute container-owned children in the current routine frame."""

        routine = _active_routine.get()
        execution = current_cancellation()
        if routine is None or execution is None:
            raise ValueError("Nested tasks require an active routine execution.")
        results, _flow_action = self._execute_task_sequence(
            routine,
            tasks,
            trigger,
            execution,
        )
        return tuple(results)

    def _execute_task_sequence(
        self,
        routine: RoutineDefinition,
        tasks: tuple[TaskDefinition, ...],
        trigger: TriggerEvent,
        execution: AutomationCancellation,
    ) -> tuple[list[TaskExecutionResult], str]:
        results: list[TaskExecutionResult] = []
        flow_action = ""
        for task in tasks:
            if execution.cancelled:
                break
            if not task.enabled:
                continue
            self._refresh_registry_values(trigger)
            task_result = self._execute_task(routine, task, trigger)
            results.append(task_result)
            if execution.cancelled or not task_result.succeeded:
                break
            if task_result.flow_action:
                flow_action = task_result.flow_action
                break
        return results, flow_action

    @staticmethod
    def safe_execution_context(
        context,
    ) -> tuple[tuple[str, str], ...]:
        """Capture useful routine context without retaining credentials.

        Run History is intentionally an allowlist, not a dump of registry or
        task configuration state. Values are copied at execution time so the
        details window never substitutes a later live Variable value.
        """
        allowed_prefixes = (
            "user.",
            "chat.",
            "command.",
            "keyword.",
            "event.",
            "obs.",
            "ads.requester.",
            "subscription.",
            "raid.",
            "automation.",
        )
        sensitive_parts = {
            "api_key",
            "apikey",
            "authorization",
            "credential",
            "credentials",
            "oauth",
            "password",
            "relay_key",
            "secret",
            "token",
        }
        captured: list[tuple[str, str]] = []
        for raw_name, raw_value in context.items():
            name = str(raw_name).strip().casefold()
            if not name.startswith(allowed_prefixes):
                continue
            parts = set(name.replace("-", "_").split("."))
            if parts & sensitive_parts or any(
                part.endswith("_token") or part.endswith("_secret")
                for part in parts
            ):
                continue
            captured.append((name, str(raw_value)))
        return tuple(sorted(captured))

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
        cancellation = current_cancellation()
        if cancellation is not None and cancellation.cancelled:
            task_result = replace(
                task_result,
                succeeded=False,
                detail=cancellation.reason,
                cancelled=True,
            )
        Events.emit(
            "task_cancelled"
            if task_result.cancelled
            else "task_completed"
            if task_result.succeeded
            else "task_failed",
            trigger=trigger,
            routine=routine,
            task=task,
            result=task_result,
        )
        return task_result
