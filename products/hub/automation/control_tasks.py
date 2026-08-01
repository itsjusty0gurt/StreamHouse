from __future__ import annotations

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.core.events import Events


CONTROL_TASK_LABELS = {
    "core.set_routine_state": "Core - Enable, disable, or toggle routine",
    "core.set_task_state": "Core - Enable, disable, or toggle task",
    "core.set_queue_state": "Core - Pause, resume, or toggle queue",
    "core.clear_queue": "Core - Clear queue",
}


def _result(task: TaskDefinition, succeeded: bool, detail: str) -> TaskExecutionResult:
    return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)


def _enabled_for(action: str, current: bool) -> bool:
    action = action.strip().casefold()
    if action == "toggle":
        return not current
    if action in {"enable", "resume"}:
        return True
    if action in {"disable", "pause"}:
        return False
    raise ValueError("Choose enable, disable, toggle, pause, or resume.")


class SetRoutineStateTask:
    task_type = "core.set_routine_state"

    def __init__(self, routine_store: RoutineStore) -> None:
        self.routine_store = routine_store

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            routine_id = str(task.config.get("routine_id", "")).strip()
            routine = self.routine_store.get(routine_id)
            if routine is None:
                raise ValueError("Choose a routine.")
            action = str(task.config.get("action", "toggle"))
            enabled = _enabled_for(action, routine.enabled)
            self.routine_store.update(routine.routine_id, enabled=enabled)
            return _result(
                task,
                True,
                f'{"Enabled" if enabled else "Disabled"} routine "{routine.name}".',
            )
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class SetTaskStateTask:
    task_type = "core.set_task_state"

    def __init__(self, routine_store: RoutineStore) -> None:
        self.routine_store = routine_store

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            routine_id = str(task.config.get("routine_id", "")).strip()
            task_id = str(task.config.get("task_id", "")).strip()
            routine = self.routine_store.get(routine_id)
            if routine is None:
                raise ValueError("Choose a routine.")
            target = next(
                (value for value in routine.tasks if value.task_id == task_id),
                None,
            )
            if target is None:
                raise ValueError("Choose a task.")
            action = str(task.config.get("action", "toggle"))
            enabled = _enabled_for(action, target.enabled)
            self.routine_store.update_task(
                routine.routine_id,
                target.task_id,
                enabled=enabled,
            )
            return _result(
                task,
                True,
                f'{"Enabled" if enabled else "Disabled"} task "{target.name}".',
            )
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class SetQueueStateTask:
    task_type = "core.set_queue_state"

    def __init__(self, queue_store: AutomationQueueStore) -> None:
        self.queue_store = queue_store

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            queue_id = str(task.config.get("queue_id", "")).strip()
            queue = self.queue_store.get(queue_id)
            if queue is None:
                raise ValueError("Choose a queue.")
            action = str(task.config.get("action", "toggle"))
            running = not queue.paused
            should_run = _enabled_for(action, running)
            updated = self.queue_store.update(queue.queue_id, paused=not should_run)
            Events.emit("automation_queue_changed", queue_id=updated.queue_id)
            return _result(
                task,
                True,
                f'{"Resumed" if should_run else "Paused"} queue "{updated.name}".',
            )
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class ClearQueueTask:
    task_type = "core.clear_queue"

    def __init__(
        self,
        queue_store: AutomationQueueStore,
        queue_manager: AutomationQueueManager,
    ) -> None:
        self.queue_store = queue_store
        self.queue_manager = queue_manager

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            queue_id = str(task.config.get("queue_id", "")).strip()
            queue = self.queue_store.get(queue_id)
            if queue is None:
                raise ValueError("Choose a queue.")
            count = self.queue_manager.clear(queue.queue_id)
            Events.emit("automation_queue_changed", queue_id=queue.queue_id)
            return _result(
                task,
                True,
                f'Cleared {count} pending item(s) from "{queue.name}".',
            )
        except (TypeError, ValueError) as error:
            return _result(task, False, str(error))


def register_control_tasks(
    registry,
    routine_store: RoutineStore,
    queue_store: AutomationQueueStore,
    queue_manager: AutomationQueueManager,
) -> None:
    registry.register(SetRoutineStateTask(routine_store))
    registry.register(SetTaskStateTask(routine_store))
    registry.register(SetQueueStateTask(queue_store))
    registry.register(ClearQueueTask(queue_store, queue_manager))
