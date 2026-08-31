from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import MutableMapping

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import (
    RoutineExecutionResult,
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_outputs import automation_output_name
from products.hub.automation.variable_registry import render_placeholders, validate_variable_name


VARIABLE_TASK_LABELS = {
    "core.create_global_variable": "Core — Create global variable",
    "core.create_session_variable": "Core — Create session variable",
    "core.create_routine_variable": "Core — Create routine variable",
    "core.delete_variable": "Core — Delete variable",
    "core.adjust_variable": "Core — Increment or decrement variable",
    "core.toggle_variable": "Core — Toggle variable",
    "core.run_routine": "Core — Run routine",
}
VARIABLE_MANAGEMENT_TASK_TYPES = frozenset(
    task_type
    for task_type in VARIABLE_TASK_LABELS
    if task_type != "core.run_routine"
)


def _result(task: TaskDefinition, succeeded: bool, detail: str) -> TaskExecutionResult:
    return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)


def _context(trigger: TriggerEvent) -> MutableMapping[str, str]:
    if not isinstance(trigger.context, MutableMapping):
        raise ValueError("This automation execution does not have a writable context.")
    return trigger.context


class CreateVariableTask:
    def __init__(self, store: CustomVariableStore, scope: str) -> None:
        self.store = store
        self.scope = scope
        self.task_type = f"core.create_{scope}_variable"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            name = self.store.validate_name(str(task.config.get("name", "")))
            value = render_placeholders(str(task.config.get("value", "")), context, strip_values=True)
            if self.scope != "routine":
                self.store.set(self.scope, name, value)
            context[
                automation_output_name(name)
                if self.scope == "routine"
                else f"custom.{name}"
            ] = value
            return _result(task, True, f'Set {self.scope} variable "{name}".')
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class DeleteVariableTask:
    task_type = "core.delete_variable"

    def __init__(self, store: CustomVariableStore) -> None:
        self.store = store

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            name = validate_variable_name(str(task.config.get("name", "")))
            if not name.startswith("custom."):
                raise ValueError("Only custom.* variables can be deleted.")
            bare = self.store.validate_custom_name(name)
            existed = name in context or bool(self.store.scope_of(bare))
            self.store.delete(name)
            context.pop(name, None)
            if not existed:
                raise ValueError(f'Variable "{name}" does not exist.')
            return _result(task, True, f'Deleted variable "{name}".')
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class AdjustVariableTask:
    task_type = "core.adjust_variable"

    def __init__(self, store: CustomVariableStore) -> None:
        self.store = store

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            name = validate_variable_name(str(task.config.get("name", "")))
            if not name.startswith(("custom.", "automation.")):
                raise ValueError("Only custom.* or automation.* variables can be adjusted.")
            if name not in context:
                raise ValueError(f'Variable "{name}" does not exist.')
            current = float(str(context[name]).strip())
            amount = float(task.config.get("amount", 1))
            value = current + amount
            rendered = str(int(value)) if value.is_integer() else f"{value:g}"
            bare = name.split(".", 1)[1]
            scope = self.store.scope_of(bare) if name.startswith("custom.") else ""
            if scope:
                self.store.set(scope, bare, rendered)
            context[name] = rendered
            return _result(task, True, f'Variable "{name}" is now {rendered}.')
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class ToggleVariableTask:
    task_type = "core.toggle_variable"
    TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
    FALSE_VALUES = frozenset({"0", "false", "no", "off"})

    def __init__(self, store: CustomVariableStore) -> None:
        self.store = store

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            name = validate_variable_name(str(task.config.get("name", "")))
            if not name.startswith(("custom.", "automation.")):
                raise ValueError("Only custom.* or automation.* variables can be toggled.")
            if name not in context:
                raise ValueError(f'Variable "{name}" does not exist.')
            current = str(context[name]).strip().casefold()
            if current in self.TRUE_VALUES:
                value = "false"
            elif current in self.FALSE_VALUES:
                value = "true"
            else:
                raise ValueError(f'Variable "{name}" is not a true/false value.')
            bare = name.split(".", 1)[1]
            scope = self.store.scope_of(bare) if name.startswith("custom.") else ""
            if scope:
                self.store.set(scope, bare, value)
            context[name] = value
            return _result(task, True, f'Variable "{name}" is now {value}.')
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))


class RunRoutineTask:
    task_type = "core.run_routine"

    def __init__(
        self,
        runner: Callable[[str, TriggerEvent], RoutineExecutionResult],
        routine_name: Callable[[str], str] | None = None,
    ) -> None:
        self.runner = runner
        self.routine_name = routine_name or (lambda routine_id: routine_id)

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        routine_id = str(task.config.get("routine_id", "")).strip()
        if not routine_id:
            return _result(task, False, "Choose a routine to run.")
        nested = self.runner(routine_id, trigger)
        name = self.routine_name(routine_id) or routine_id
        task_details = "; ".join(
            result.detail or result.task_type
            for result in nested.task_results
        )
        summary = f'Nested routine "{name}"'
        if task_details:
            summary += f": {task_details}"
        stop_on_failure = bool(task.config.get("stop_on_failure", True))
        if nested.succeeded:
            return replace(
                _result(task, True, summary),
                nested_results=(nested,),
            )
        failure_detail = summary
        if nested.detail:
            failure_detail += f". {nested.detail}"
        if stop_on_failure:
            result = _result(task, False, failure_detail)
        else:
            result = _result(
                task,
                True,
                failure_detail + " Parent routine continued.",
            )
        return replace(
            result,
            nested_results=(nested,),
        )


def register_variable_tasks(
    registry: TaskRegistry,
    store: CustomVariableStore,
) -> None:
    registry.register(CreateVariableTask(store, "global"))
    registry.register(CreateVariableTask(store, "session"))
    registry.register(CreateVariableTask(store, "routine"))
    registry.register(DeleteVariableTask(store))
    registry.register(AdjustVariableTask(store))
    registry.register(ToggleVariableTask(store))
