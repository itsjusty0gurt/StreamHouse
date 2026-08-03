from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.counters.models import SCOPES
from products.hub.counters.service import CounterOperation, CounterService

COUNTER_TASK_LABELS = {
    "counter.update": "Counter — Update",
    "counter.get_value": "Counter — Get value",
    "counter.set_value": "Counter — Set value",
    "counter.reset": "Counter — Reset",
    "counter.get_leaderboard": "Counter — Get leaderboard",
}
OUTPUT_SUFFIXES = (
    "amount_changed", "channel_total", "stream_total", "viewer_total",
    "viewer_stream_total", "viewer_rank", "viewer_display_name", "leaderboard",
    "leaderboard_entries", "top_viewer_id", "top_viewer_display_name",
    "top_viewer_value", "updated_scopes", "status", "formatted_value",
    "channel_total_status", "stream_total_status", "viewer_total_status",
    "viewer_stream_total_status",
)
TEMPLATE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def output_prefix(config: dict[str, Any]) -> str:
    return CustomVariableStore.validate_generated_name(str(config.get("output_prefix") or config.get("counter_id", "")))


def generated_names(config: dict[str, Any]) -> tuple[str, ...]:
    try:
        prefix = output_prefix(config)
    except ValueError:
        return ()
    return tuple(f"{prefix}_{suffix}" for suffix in OUTPUT_SUFFIXES)


class CounterTask:
    task_type = ""

    def __init__(self, service: CounterService, stream_id_provider: Callable[[], str]) -> None:
        self.service = service
        self.stream_id_provider = stream_id_provider

    @staticmethod
    def _context(trigger: TriggerEvent) -> dict[str, str]:
        if not isinstance(trigger.context, dict):
            raise ValueError("Counter task output requires a mutable routine context.")
        return trigger.context

    @staticmethod
    def _viewer(config: dict[str, Any], context: dict[str, str]) -> tuple[str, str, str]:
        source = str(config.get("viewer_source", "trigger")).casefold()
        if source == "none":
            return "", "", ""
        if source == "target":
            return (str(context.get("target_user_id", "")).strip(), str(context.get("target_login", "")).strip(), str(context.get("target_display_name", "")).strip())
        return (str(context.get("user_id", "")).strip(), str(context.get("user_login", context.get("user", ""))).strip(), str(context.get("user", "")).strip())

    @staticmethod
    def _integer(value: Any, context: dict[str, str]) -> int:
        rendered = TEMPLATE.sub(lambda match: str(context.get(match.group(1), "")), str(value)).strip()
        try:
            return int(rendered)
        except ValueError as error:
            raise ValueError(f'Counter value "{rendered}" is not an integer.') from error

    def _publish(self, task: TaskDefinition, context: dict[str, str], operation: CounterOperation, *, leaderboard: str = "", top: dict[str, Any] | None = None) -> None:
        prefix = output_prefix(task.config)
        values = operation.values
        top = top or {}
        definition = self.service.get_counter(str(task.config.get("counter_id", "")))
        formatted = self.service.format_value(definition.counter_id, values.viewer_total if values.viewer_display_name else values.channel_total) if definition else ""
        context.update({
            f"{prefix}_amount_changed": str(operation.amount_changed), f"{prefix}_channel_total": str(values.channel_total),
            f"{prefix}_stream_total": str(values.stream_total), f"{prefix}_viewer_total": str(values.viewer_total),
            f"{prefix}_viewer_stream_total": str(values.viewer_stream_total), f"{prefix}_viewer_rank": str(values.viewer_rank),
            f"{prefix}_viewer_display_name": values.viewer_display_name, f"{prefix}_leaderboard": leaderboard,
            f"{prefix}_leaderboard_entries": str(len([part for part in leaderboard.split(" | ") if part])),
            f"{prefix}_top_viewer_id": str(top.get("user_id", "")), f"{prefix}_top_viewer_display_name": str(top.get("display_name", top.get("login", ""))),
            f"{prefix}_top_viewer_value": str(top.get("value", "")), f"{prefix}_updated_scopes": ", ".join(operation.updated_scopes),
            f"{prefix}_status": operation.status, f"{prefix}_formatted_value": formatted,
            f"{prefix}_channel_total_status": "unavailable" if "channel_total" in operation.skipped_scopes else "available",
            f"{prefix}_stream_total_status": "unavailable" if "stream_total" in operation.skipped_scopes else "available",
            f"{prefix}_viewer_total_status": "unavailable" if "viewer_total" in operation.skipped_scopes else "available",
            f"{prefix}_viewer_stream_total_status": "unavailable" if "viewer_stream_total" in operation.skipped_scopes else "available",
        })

    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        raise NotImplementedError

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            operation = self._run(task, trigger)
            context = self._context(trigger)
            self._publish(task, context, operation)
            succeeded = operation.status not in {"disabled", "skipped", "skipped_bot", "error"}
            return TaskExecutionResult(task.task_id, task.task_type, succeeded, operation.detail or operation.status.replace("_", " ").title())
        except (KeyError, OSError, TypeError, ValueError) as error:
            try:
                prefix = output_prefix(task.config)
                self._context(trigger)[f"{prefix}_status"] = "error"
            except ValueError:
                pass
            return TaskExecutionResult(task.task_id, task.task_type, False, str(error))


class UpdateCounterTask(CounterTask):
    task_type = "counter.update"
    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        user_id, login, display = self._viewer(task.config, context)
        scopes = [scope for scope in SCOPES if bool(task.config.get(scope, False))]
        return self.service.update_values(str(task.config.get("counter_id", "")), self._integer(task.config.get("amount", "1"), context), scopes, user_id=user_id, login=login, display_name=display, stream_id=self.stream_id_provider())


class GetCounterValueTask(CounterTask):
    task_type = "counter.get_value"
    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        user_id, _, _ = self._viewer(task.config, context)
        stream_id = self.stream_id_provider()
        values = self.service.get_values(str(task.config.get("counter_id", "")), user_id=user_id, stream_id=stream_id)
        skipped = ("stream_total", "viewer_stream_total") if not stream_id else ()
        return CounterOperation("available" if stream_id else "partial", values, skipped_scopes=skipped)


class SetCounterValueTask(CounterTask):
    task_type = "counter.set_value"
    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        user_id, login, display = self._viewer(task.config, context)
        return self.service.set_value(str(task.config.get("counter_id", "")), str(task.config.get("scope", "channel_total")), self._integer(task.config.get("value", "0"), context), user_id=user_id, login=login, display_name=display, stream_id=self.stream_id_provider())


class ResetCounterTask(CounterTask):
    task_type = "counter.reset"
    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        user_id, _, _ = self._viewer(task.config, context)
        scopes = [scope for scope in SCOPES if bool(task.config.get(scope, False))]
        return self.service.reset(str(task.config.get("counter_id", "")), scopes, user_id=user_id, stream_id=self.stream_id_provider(), all_viewers=bool(task.config.get("all_viewers", False)))


class GetCounterLeaderboardTask(CounterTask):
    task_type = "counter.get_leaderboard"
    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            counter_id = str(task.config.get("counter_id", ""))
            current = str(task.config.get("viewer_scope", "lifetime")) == "current_stream"
            stream_id = self.stream_id_provider()
            if current and not stream_id:
                prefix = output_prefix(task.config)
                self._context(trigger)[f"{prefix}_status"] = "unavailable"
                return TaskExecutionResult(task.task_id, task.task_type, False, "A live Twitch stream is required for a current-stream leaderboard.")
            rows = self.service.leaderboard(counter_id, stream_id=stream_id, current_stream=current, limit=int(task.config.get("limit", 5)), include_zero=bool(task.config.get("include_zero", False)))
            key = "stream_total" if current else "total"
            parts = [f"{index}. {row['display_name'] or row['login'] or row['user_id']}: {row[key]:,}" for index, row in enumerate(rows, 1)]
            message = " | ".join(parts)[:480]
            top = dict(rows[0]) if rows else {}
            if top: top["value"] = top[key]
            values = self.service.get_values(counter_id, stream_id=self.stream_id_provider())
            operation = CounterOperation("available" if rows else "empty", values)
            self._publish(task, self._context(trigger), operation, leaderboard=message, top=top)
            return TaskExecutionResult(task.task_id, task.task_type, True, f"Built {len(rows)} leaderboard entries.")
        except (KeyError, OSError, TypeError, ValueError) as error:
            try:
                self._context(trigger)[f"{output_prefix(task.config)}_status"] = "error"
            except ValueError:
                pass
            return TaskExecutionResult(task.task_id, task.task_type, False, str(error))


def register_counter_tasks(registry, service: CounterService, stream_id_provider: Callable[[], str]) -> None:
    for handler in (UpdateCounterTask, GetCounterValueTask, SetCounterValueTask, ResetCounterTask, GetCounterLeaderboardTask):
        registry.register(handler(service, stream_id_provider))
