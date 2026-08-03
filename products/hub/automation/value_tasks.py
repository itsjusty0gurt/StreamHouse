from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Mapping

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import (
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.variables import render_preview


VALUE_TASK_LABELS = {
    "core.format_duration": "Core — Format duration",
    "core.select_text": "Core — Select text by value",
}


def _context(trigger: TriggerEvent) -> dict[str, str]:
    if not isinstance(trigger.context, dict):
        raise ValueError("Automation task output requires a mutable routine context.")
    return trigger.context


def _result(task: TaskDefinition, succeeded: bool, detail: str) -> TaskExecutionResult:
    return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)


def _parse_datetime(value: str) -> datetime | None:
    clean = value.strip()
    if not clean or clean == "--":
        return None
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    parsed = datetime.fromisoformat(clean)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _add_years(value: datetime, years: int) -> datetime:
    year = value.year + years
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def format_readable_duration(start: datetime, end: datetime) -> str:
    """Return the two most significant calendar-aware duration units."""
    if end < start:
        raise ValueError("The duration end cannot be before its start.")
    cursor = start
    years = max(end.year - cursor.year, 0)
    while years and _add_years(cursor, years) > end:
        years -= 1
    cursor = _add_years(cursor, years)
    months = max((end.year - cursor.year) * 12 + end.month - cursor.month, 0)
    while months and _add_months(cursor, months) > end:
        months -= 1
    cursor = _add_months(cursor, months)
    remainder = end - cursor
    days = remainder.days
    seconds = remainder.seconds
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    values = (
        (years, "year"),
        (months, "month"),
        (days, "day"),
        (hours, "hour"),
        (minutes, "minute"),
        (seconds, "second"),
    )
    parts = [
        f"{amount} {label}{'' if amount == 1 else 's'}"
        for amount, label in values
        if amount
    ][:2]
    return " ".join(parts) or "0 seconds"


class FormatDurationTask:
    task_type = "core.format_duration"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _context(trigger)
        try:
            output = CustomVariableStore.validate_generated_name(
                str(task.config.get("output_variable", "formatted_duration"))
            )
        except ValueError as error:
            return _result(task, False, str(error))
        status_name = f"{output}_status"
        context[output] = ""
        context[status_name] = "missing"
        try:
            seconds_template = str(task.config.get("seconds", "")).strip()
            if seconds_template:
                seconds = float(render_preview(seconds_template, context))
                if seconds < 0:
                    context[status_name] = "future"
                    return _result(task, True, "Duration is in the future.")
                end = datetime.now(timezone.utc)
                start = end - timedelta(seconds=seconds)
            else:
                start = _parse_datetime(
                    render_preview(str(task.config.get("start", "")), context)
                )
                if start is None:
                    return _result(task, True, "Duration start is unavailable.")
                end_template = str(task.config.get("end", "")).strip()
                end = (
                    _parse_datetime(render_preview(end_template, context))
                    if end_template
                    else datetime.now(timezone.utc)
                )
                if end is None:
                    return _result(task, True, "Duration end is unavailable.")
            if end < start:
                context[status_name] = "future"
                return _result(task, True, "Duration is in the future.")
            context[output] = format_readable_duration(start, end)
            context[status_name] = "available"
            return _result(task, True, f"Formatted duration as {context[output]}.")
        except (TypeError, ValueError, OverflowError):
            context[status_name] = "invalid"
            return _result(task, True, "Duration value is invalid.")


class SelectTextTask:
    task_type = "core.select_text"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _context(trigger)
        try:
            output = CustomVariableStore.validate_generated_name(
                str(task.config.get("output_variable", "selected_text"))
            )
        except ValueError as error:
            return _result(task, False, str(error))
        selector = render_preview(
            str(task.config.get("selector", "")), context
        ).strip().casefold()
        raw_cases = task.config.get("cases", {})
        cases: Mapping[str, object] = raw_cases if isinstance(raw_cases, dict) else {}
        selected = next(
            (
                value
                for key, value in cases.items()
                if str(key).strip().casefold() == selector
            ),
            task.config.get("default", ""),
        )
        context[output] = render_preview(str(selected), context).strip()
        if not context[output]:
            return _result(task, False, "The selected text is empty.")
        return _result(task, True, f'Selected text for "{selector or "(empty)"}".')


def register_value_tasks(registry) -> None:
    registry.register(FormatDurationTask())
    registry.register(SelectTextTask())
