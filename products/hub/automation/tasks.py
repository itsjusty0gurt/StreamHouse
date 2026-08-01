from __future__ import annotations

from typing import Protocol

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent


class TaskHandler(Protocol):
    task_type: str

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult: ...


class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, handler: TaskHandler) -> None:
        clean_type = handler.task_type.strip().casefold()
        if not clean_type:
            raise ValueError("Task handlers require a task type.")
        self._handlers[clean_type] = handler

    def unregister(self, task_type: str) -> bool:
        return self._handlers.pop(task_type.strip().casefold(), None) is not None

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        handler = self._handlers.get(task.task_type.strip().casefold())
        if handler is None:
            return TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail=f"No task provider is registered for {task.task_type}.",
            )
        return handler.execute(task, trigger)
