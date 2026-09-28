from __future__ import annotations

import random
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import MutableMapping

from PySide6.QtWidgets import QInputDialog

from products.hub.automation.models import (
    END_ROUTINE_ACTION,
    RoutineExecutionResult,
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.variable_registry import placeholder_names, render_placeholders
from products.hub.automation.variable_registry import VariableDataType
from products.hub.automation.variable_outputs import automation_output_name


LOGIC_TASK_LABELS = {
    "core.end_routine": "Core — End Routine",
    "core.logic_get_input": "Core — Get input",
    "core.logic_random_number": "Core — Get random number",
    "core.logic_random_choice": "Core — Random choice",
    "core.if": "Core — If",
    "core.logic_switch": "Core — Switch",
    "core.logic_while": "Core — While",
}

COMPARISON_CHOICES = (
    ("Equals", "equals"),
    ("Does not equal", "not_equals"),
    ("Equals (ignore case)", "equals_ignore_case"),
    ("Contains", "contains"),
    ("Does not contain", "not_contains"),
    ("Starts with", "starts_with"),
    ("Ends with", "ends_with"),
    ("Regular expression matches", "regex"),
    ("Less than", "less_than"),
    ("Less than or equal", "less_or_equal"),
    ("Greater than", "greater_than"),
    ("Greater than or equal", "greater_or_equal"),
    ("Is empty", "is_empty"),
    ("Is true", "is_true"),
    ("Is false", "is_false"),
    ("Variable exists", "exists"),
    ("Variable does not exist", "not_exists"),
)
UNARY_OPERATORS = frozenset(
    {"is_empty", "is_true", "is_false", "exists", "not_exists"}
)

IF_COMPARISON_CHOICES = (
    ("Equals", "equals"),
    ("Does Not Equal", "not_equals"),
    ("Contains", "contains"),
    ("Does Not Contain", "not_contains"),
    ("Starts With", "starts_with"),
    ("Ends With", "ends_with"),
    ("Greater Than", "greater_than"),
    ("Greater Than or Equal", "greater_or_equal"),
    ("Less Than", "less_than"),
    ("Less Than or Equal", "less_or_equal"),
    ("Is Empty", "is_empty"),
    ("Is Not Empty", "is_not_empty"),
)
IF_UNARY_OPERATORS = frozenset({"is_empty", "is_not_empty"})


def comparison_choices_for_type(
    data_type: VariableDataType | None,
) -> tuple[tuple[str, str], ...]:
    """Return operators supported by the existing condition evaluator for a type."""
    if data_type is None:
        return COMPARISON_CHOICES
    allowed = {
        VariableDataType.TEXT: {
            "equals", "not_equals", "equals_ignore_case", "contains",
            "not_contains", "starts_with", "ends_with", "regex", "is_empty",
            "exists", "not_exists",
        },
        VariableDataType.INTEGER: {
            "equals", "not_equals", "less_than", "less_or_equal",
            "greater_than", "greater_or_equal", "exists", "not_exists",
        },
        VariableDataType.NUMBER: {
            "equals", "not_equals", "less_than", "less_or_equal",
            "greater_than", "greater_or_equal", "exists", "not_exists",
        },
        VariableDataType.BOOLEAN: {
            "equals", "not_equals", "is_true", "is_false", "exists", "not_exists",
        },
        VariableDataType.DATETIME: {
            "equals", "not_equals", "exists", "not_exists",
        },
    }[data_type]
    return tuple(choice for choice in COMPARISON_CHOICES if choice[1] in allowed)


def _result(
    task: TaskDefinition,
    succeeded: bool,
    detail: str,
    *,
    flow_action: str = "",
    nested_results: tuple[RoutineExecutionResult, ...] = (),
    child_results: tuple[TaskExecutionResult, ...] = (),
    selected_branch: str = "",
) -> TaskExecutionResult:
    return TaskExecutionResult(
        task.task_id,
        task.task_type,
        succeeded,
        detail,
        flow_action=flow_action,
        nested_results=nested_results,
        child_results=child_results,
        selected_branch=selected_branch,
    )


def _context(trigger: TriggerEvent) -> MutableMapping[str, str]:
    if not isinstance(trigger.context, MutableMapping):
        raise ValueError("This automation execution does not have a writable context.")
    return trigger.context


def _variable_name(value: str) -> str:
    clean = value.strip()
    if clean.startswith("{") and clean.endswith("}"):
        clean = clean[1:-1]
    return clean.strip().casefold()


def evaluate_condition(
    left_template: str,
    operator: str,
    right_template: str,
    context: MutableMapping[str, str],
) -> bool:
    operation = operator.strip().casefold()
    if operation in {"exists", "not_exists"}:
        exists = _variable_name(left_template) in context
        return exists if operation == "exists" else not exists
    left = render_placeholders(left_template, context, strip_values=True)
    right = render_placeholders(right_template, context, strip_values=True)
    if operation == "equals":
        return left == right
    if operation == "not_equals":
        return left != right
    if operation == "equals_ignore_case":
        return left.casefold() == right.casefold()
    if operation == "contains":
        return right in left
    if operation == "not_contains":
        return right not in left
    if operation == "starts_with":
        return left.startswith(right)
    if operation == "ends_with":
        return left.endswith(right)
    if operation == "regex":
        return re.search(right, left) is not None
    if operation in {"less_than", "less_or_equal", "greater_than", "greater_or_equal"}:
        left_number = float(left)
        right_number = float(right)
        if operation == "less_than":
            return left_number < right_number
        if operation == "less_or_equal":
            return left_number <= right_number
        if operation == "greater_than":
            return left_number > right_number
        return left_number >= right_number
    if operation == "is_empty":
        return not left.strip()
    if operation == "is_true":
        return left.strip().casefold() in {"1", "true", "yes", "on"}
    if operation == "is_false":
        return left.strip().casefold() in {"0", "false", "no", "off", ""}
    raise ValueError(f"Unsupported logic comparison: {operator}")


def evaluate_if_condition(
    left_template: str,
    operator: str,
    right_template: str,
    context: MutableMapping[str, str],
    *,
    ignore_case: bool = False,
) -> bool:
    operation = operator.strip().casefold()
    supported = {value for _label, value in IF_COMPARISON_CHOICES}
    if operation not in supported:
        raise ValueError(f"Unsupported If comparison: {operator}")
    templates = (left_template,) if operation in IF_UNARY_OPERATORS else (
        left_template,
        right_template,
    )
    for template in templates:
        missing = [name for name in placeholder_names(template) if name not in context]
        if missing:
            raise ValueError(f'Variable "{{{missing[0]}}}" is not available for this If.')
    left = render_placeholders(left_template, context, strip_values=True)
    right = render_placeholders(right_template, context, strip_values=True)
    if operation == "is_empty":
        return not left.strip()
    if operation == "is_not_empty":
        return bool(left.strip())
    if operation in {"less_than", "less_or_equal", "greater_than", "greater_or_equal"}:
        try:
            left_number = Decimal(left)
            right_number = Decimal(right)
        except (InvalidOperation, ValueError):
            raise ValueError(
                f'If comparison requires numbers, but received "{left}" and "{right}".'
            ) from None
        if not left_number.is_finite() or not right_number.is_finite():
            raise ValueError("If numeric comparisons require finite numbers.")
        if operation == "less_than":
            return left_number < right_number
        if operation == "less_or_equal":
            return left_number <= right_number
        if operation == "greater_than":
            return left_number > right_number
        return left_number >= right_number
    if ignore_case:
        left, right = left.casefold(), right.casefold()
    if operation == "equals":
        return left == right
    if operation == "not_equals":
        return left != right
    if operation == "contains":
        return right in left
    if operation == "not_contains":
        return right not in left
    if operation == "starts_with":
        return left.startswith(right)
    if operation == "ends_with":
        return left.endswith(right)
    raise ValueError(f"Unsupported If comparison: {operator}")


class EndRoutineTask:
    task_type = "core.end_routine"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        return _result(
            task,
            True,
            "End Routine was reached; remaining tasks were skipped.",
            flow_action=END_ROUTINE_ACTION,
        )


class GetInputTask:
    task_type = "core.logic_get_input"

    def __init__(self, input_provider=None) -> None:
        self.input_provider = input_provider or QInputDialog.getText

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            name = automation_output_name(task.config.get("name", ""))
            title = render_placeholders(
                str(task.config.get("title", "Streamhouse Hub Input")), context
            )
            prompt = render_placeholders(str(task.config.get("prompt", "Enter a value:")), context, strip_values=True)
            default = render_placeholders(str(task.config.get("default", "")), context, strip_values=True)
            value, accepted = self.input_provider(None, title, prompt, text=default)
            context[name] = str(value) if accepted else ""
            context[automation_output_name(name, "accepted")] = "true" if accepted else "false"
            if not accepted and bool(task.config.get("break_on_cancel", False)):
                return _result(
                    task,
                    True,
                    "Input was cancelled; routine stopped.",
                    flow_action=END_ROUTINE_ACTION,
                )
            return _result(
                task,
                True,
                f'Stored input in "{name}".' if accepted else "Input was cancelled.",
            )
        except (TypeError, ValueError) as error:
            return _result(task, False, str(error))


class GetRandomNumberTask:
    task_type = "core.logic_random_number"

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            name = automation_output_name(task.config.get("name", ""))
            mode = str(task.config.get("mode", "integer"))
            if mode == "decimal":
                value = f"{self.rng.random():.6f}".rstrip("0").rstrip(".")
            else:
                minimum = int(task.config.get("minimum", 0))
                maximum = int(task.config.get("maximum", 100))
                if minimum > maximum:
                    raise ValueError("Random-number minimum cannot exceed maximum.")
                value = str(self.rng.randint(minimum, maximum))
            context[name] = value
            return _result(task, True, f'Random value "{name}" is {value}.')
        except (TypeError, ValueError) as error:
            return _result(task, False, str(error))


class _RoutineLogicTask:
    def __init__(
        self,
        runner: Callable[[str, TriggerEvent], RoutineExecutionResult],
        routine_name: Callable[[str], str] | None = None,
    ) -> None:
        self.runner = runner
        self.routine_name = routine_name or (lambda routine_id: routine_id)

    def _run(self, routine_id: str, trigger: TriggerEvent) -> RoutineExecutionResult:
        return self.runner(routine_id, trigger)


class RandomChoiceTask(_RoutineLogicTask):
    task_type = "core.logic_random_choice"

    def __init__(
        self,
        runner: Callable[[str, TriggerEvent], RoutineExecutionResult],
        routine_name: Callable[[str], str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(runner, routine_name)
        self.rng = rng or random.Random()

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            raw_choices = task.config.get("choices", [])
            if not isinstance(raw_choices, list):
                raise ValueError("Random choices are invalid.")
            choices: list[tuple[str, str, float]] = []
            for entry in raw_choices:
                if not isinstance(entry, dict):
                    continue
                routine_id = str(entry.get("routine_id", "")).strip()
                weight = float(entry.get("weight", 1))
                if not routine_id or weight <= 0:
                    continue
                label = str(entry.get("label", "")).strip()
                choices.append((label, routine_id, weight))
            if not choices:
                raise ValueError("Add at least one random choice with a positive weight.")
            selected = self.rng.choices(
                choices,
                weights=[choice[2] for choice in choices],
                k=1,
            )[0]
            label, routine_id, _weight = selected
            result = self._run(routine_id, trigger)
            display_name = label or self.routine_name(routine_id) or routine_id
            if not result.succeeded:
                return _result(
                    task,
                    False,
                    result.detail or f'Random choice "{display_name}" failed.',
                    nested_results=(result,),
                )
            return _result(
                task,
                True,
                f'Random choice selected "{display_name}".',
                nested_results=(result,),
            )
        except (TypeError, ValueError) as error:
            return _result(task, False, str(error))


class IfTask:
    task_type = "core.if"

    def __init__(
        self,
        branch_runner: Callable[
            [tuple[TaskDefinition, ...], TriggerEvent],
            tuple[TaskExecutionResult, ...],
        ],
    ) -> None:
        self.branch_runner = branch_runner

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            matched = evaluate_if_condition(
                str(task.config.get("left", "")),
                str(task.config.get("operator", "equals")),
                str(task.config.get("right", "")),
                context,
                ignore_case=bool(task.config.get("ignore_case", False)),
            )
            branch = "then" if matched else "else"
            branch_tasks = tuple(task.then_tasks if matched else task.else_tasks)
            child_results = self.branch_runner(branch_tasks, trigger)
            failed = next((result for result in child_results if not result.succeeded), None)
            if failed is not None:
                return _result(
                    task,
                    False,
                    failed.detail or f"{branch.title()} branch task failed.",
                    child_results=child_results,
                    selected_branch=branch,
                )
            flow_action = next(
                (result.flow_action for result in child_results if result.flow_action),
                "",
            )
            return _result(
                task,
                True,
                f"Condition was {str(matched).lower()}; {branch.title()} branch selected.",
                flow_action=flow_action,
                child_results=child_results,
                selected_branch=branch,
            )
        except (TypeError, ValueError) as error:
            return _result(task, False, str(error))


class SwitchTask(_RoutineLogicTask):
    task_type = "core.logic_switch"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            value = render_placeholders(str(task.config.get("input", "")), context, strip_values=True)
            cases = task.config.get("cases", {})
            if not isinstance(cases, dict):
                raise ValueError("Switch cases are invalid.")
            ignore_case = bool(task.config.get("ignore_case", False))
            comparison = value.casefold() if ignore_case else value
            routine_id = ""
            matched_case = "Default"
            for case, target in cases.items():
                rendered_case = render_placeholders(str(case), context, strip_values=True)
                candidate = rendered_case.casefold() if ignore_case else rendered_case
                if comparison == candidate:
                    routine_id = str(target)
                    matched_case = rendered_case
                    break
            if not routine_id:
                routine_id = str(task.config.get("default_routine_id", "")).strip()
            if routine_id:
                result = self._run(routine_id, trigger)
                if not result.succeeded:
                    return _result(
                        task,
                        False,
                        result.detail or "Switch branch failed.",
                        nested_results=(result,),
                    )
                return _result(
                    task,
                    True,
                    f'Switch selected "{matched_case}".',
                    nested_results=(result,),
                )
            return _result(task, True, f'Switch selected "{matched_case}".')
        except (TypeError, ValueError) as error:
            return _result(task, False, str(error))


class WhileTask(_RoutineLogicTask):
    task_type = "core.logic_while"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            routine_id = str(task.config.get("routine_id", "")).strip()
            if not routine_id:
                raise ValueError("Choose a routine to repeat.")
            max_iterations = max(1, min(int(task.config.get("max_iterations", 100)), 10000))
            timeout_seconds = max(0.1, min(float(task.config.get("timeout_seconds", 10)), 3600))
            started = perf_counter()
            iterations = 0
            nested_results: list[RoutineExecutionResult] = []
            while evaluate_condition(
                str(task.config.get("left", "")),
                str(task.config.get("operator", "equals")),
                str(task.config.get("right", "")),
                context,
            ):
                if iterations >= max_iterations:
                    raise ValueError(f"While loop reached its {max_iterations} iteration limit.")
                if perf_counter() - started >= timeout_seconds:
                    raise ValueError(f"While loop exceeded its {timeout_seconds:g} second limit.")
                result = self._run(routine_id, trigger)
                nested_results.append(result)
                iterations += 1
                if not result.succeeded:
                    return _result(
                        task,
                        False,
                        result.detail or "While loop routine failed.",
                        nested_results=tuple(nested_results),
                    )
            return _result(
                task,
                True,
                f"While loop completed {iterations} iteration(s).",
                nested_results=tuple(nested_results),
            )
        except (TypeError, ValueError, re.error) as error:
            return _result(task, False, str(error))


def register_logic_tasks(registry, service) -> None:
    registry.register(EndRoutineTask())
    registry.register(GetInputTask())
    registry.register(GetRandomNumberTask())
    registry.register(
        RandomChoiceTask(service.run_nested_routine, service.routine_name)
    )
    registry.register(IfTask(service.execute_child_tasks))
    registry.register(SwitchTask(service.run_nested_routine, service.routine_name))
    registry.register(WhileTask(service.run_nested_routine, service.routine_name))
