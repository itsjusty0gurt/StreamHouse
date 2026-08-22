from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.variable_registry import render_placeholders
from products.hub.counters.models import parse_counter_number
from products.hub.counters.service import CounterOperation, CounterService
from shared.streamhouse_runtime.logger import Logger

COUNTER_TASK_LABELS = {
    "counter.increase": "Counter — Increase",
    "counter.decrease": "Counter — Decrease",
    "counter.set_value": "Counter — Set",
    "counter.reset": "Counter — Reset",
}


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
    def _viewer(context: dict[str, str]) -> tuple[str, str, str]:
        values = (
            context.get("user.id", context.get("user_id", "")),
            context.get("user.login", context.get("user_login", "")),
            context.get("user.display_name", context.get("user", "")),
        )
        return tuple("" if str(value).strip() in {"", "--"} else str(value).strip() for value in values)

    def _number(self, task: TaskDefinition, context: dict[str, str], key: str) -> Decimal:
        counter_id = str(task.config.get("counter_id", ""))
        definition = self.service.get_counter(counter_id)
        if definition is None:
            raise KeyError(f'Counter "{counter_id}" does not exist.')
        raw = task.config.get(key, "1" if key == "amount" else "0")
        rendered = render_placeholders(str(raw), context).strip()
        if "{" in rendered or "}" in rendered:
            raise ValueError(f'Counter {key} could not resolve all Variables: "{rendered}".')
        return parse_counter_number(rendered, definition.numeric_type)

    def _identity(self, context: dict[str, str]) -> dict[str, str]:
        user_id, login, display_name = self._viewer(context)
        return {
            "user_id": user_id,
            "login": login,
            "display_name": display_name,
            "stream_id": self.stream_id_provider(),
        }

    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        raise NotImplementedError

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            operation = self._run(task, trigger)
            succeeded = operation.status in {"success", "partial_success", "minimum_reached", "skipped_known_bot"}
            detail = operation.detail or operation.status.replace("_", " ").title()
            if not succeeded:
                Logger.warning(f'Counter task "{task.name}" failed: {detail}', source="AUTOMATION")
            return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)
        except (KeyError, OSError, TypeError, ValueError) as error:
            Logger.warning(f'Counter task "{task.name}" failed: {error}', source="AUTOMATION")
            return TaskExecutionResult(task.task_id, task.task_type, False, str(error))


class IncreaseCounterTask(CounterTask):
    task_type = "counter.increase"

    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        return self.service.update_values(
            str(task.config.get("counter_id", "")),
            abs(self._number(task, context, "amount")),
            (str(task.config.get("scope", "channel_total")),),
            **self._identity(context),
        )


class DecreaseCounterTask(CounterTask):
    task_type = "counter.decrease"

    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        return self.service.update_values(
            str(task.config.get("counter_id", "")),
            -abs(self._number(task, context, "amount")),
            (str(task.config.get("scope", "channel_total")),),
            **self._identity(context),
        )


class SetCounterValueTask(CounterTask):
    task_type = "counter.set_value"

    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        return self.service.set_value(
            str(task.config.get("counter_id", "")),
            str(task.config.get("scope", "channel_total")),
            self._number(task, context, "value"),
            **self._identity(context),
        )


class ResetCounterTask(CounterTask):
    task_type = "counter.reset"

    def _run(self, task: TaskDefinition, trigger: TriggerEvent) -> CounterOperation:
        context = self._context(trigger)
        identity = self._identity(context)
        return self.service.reset(
            str(task.config.get("counter_id", "")),
            (str(task.config.get("scope", "channel_total")),),
            user_id=identity["user_id"],
            stream_id=identity["stream_id"],
        )


def register_counter_tasks(registry, service: CounterService, stream_id_provider: Callable[[], str]) -> None:
    for handler in (
        IncreaseCounterTask,
        DecreaseCounterTask,
        SetCounterValueTask,
        ResetCounterTask,
    ):
        registry.register(handler(service, stream_id_provider))
