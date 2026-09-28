from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from products.hub.automation.core_tasks import CORE_TASK_LABELS
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_providers import context_provider, runtime_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.obs_service.tasks import OBS_TASK_LABELS
from products.hub.twitch.commands import (
    TwitchCommandTriggerDispatcher,
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerStore,
)
from products.hub.twitch.models import TwitchBadge, TwitchMessage
from products.hub.twitch.tasks import SendTwitchChatMessageTask, TWITCH_TASK_LABELS


@dataclass(frozen=True, slots=True)
class SimulatedChatSend:
    message: str
    as_bot: bool


@dataclass(frozen=True, slots=True)
class TwitchCommandSimulation:
    outcome: str
    invocation: str = ""
    trigger_id: str = ""
    routine_id: str = ""
    routine_name: str = ""
    remaining_seconds: int = 0
    context: Mapping[str, str] = field(default_factory=dict)
    sent_messages: tuple[SimulatedChatSend, ...] = ()
    task_results: tuple[TaskExecutionResult, ...] = ()
    missing_variables: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.outcome == TwitchCommandTriggerOutcome.READY.value


class DryRunTwitchService:
    def __init__(self) -> None:
        self.sent_messages: list[SimulatedChatSend] = []

    def send_message(self, message: str, *, as_bot: bool = True) -> bool:
        self.sent_messages.append(SimulatedChatSend(message, as_bot))
        return True

    def send_pinned_message(self, message: str) -> tuple[bool, bool]:
        self.sent_messages.append(SimulatedChatSend(message, True))
        return True, True

    def run_commercial(self, length: int) -> dict[str, str]:
        return {"message": f"Would run a {int(length)} second commercial."}

    def snooze_next_ad(self) -> None:
        return None

    def resolve_user_id(self, value: str) -> str:
        return value

    def moderate_user(self, *_args, **_kwargs) -> bool:
        return True

    def update_redemption_status(self, *_args, **_kwargs) -> None:
        return None


class DryRunTaskHandler:
    def __init__(self, task_type: str, label: str) -> None:
        self.task_type = task_type
        self.label = label

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        rendered = _render_config(task.config, trigger.context)
        suffix = f": {rendered}" if rendered else "."
        return TaskExecutionResult(
            task.task_id,
            task.task_type,
            True,
            f"Would run {self.label}{suffix}",
        )


class TwitchCommandSimulator:
    def __init__(
        self,
        command_store: TwitchCommandTriggerStore,
        routine_store: RoutineStore,
        *,
        live_context: Mapping[str, str] | None = None,
    ) -> None:
        self.command_store = command_store
        self.routine_store = routine_store
        self.live_context = {
            key: str(value)
            for key, value in (live_context or {}).items()
            if str(value).strip()
        }

    def simulate(
        self,
        text: str,
        *,
        username: str = "TestViewer",
        user_id: str = "test-viewer",
        user_login: str = "testviewer",
        broadcaster_user_id: str = "broadcaster",
        badges: tuple[str, ...] = (),
    ) -> TwitchCommandSimulation:
        message = TwitchMessage(
            username=username,
            text=text,
            received_at=datetime.now(timezone.utc),
            user_id=user_id,
            user_login=user_login,
            broadcaster_user_id=broadcaster_user_id,
            badges=tuple(TwitchBadge(name, "1", "") for name in badges),
        )
        dispatcher = TwitchCommandTriggerDispatcher(self.command_store)
        result = dispatcher.evaluate(message, self.live_context)
        if result.outcome is not TwitchCommandTriggerOutcome.READY:
            return TwitchCommandSimulation(
                outcome=result.outcome.value,
                invocation=result.invocation,
                trigger_id=result.trigger_id,
                routine_id=result.routine_id,
                remaining_seconds=result.remaining_seconds,
            )

        routine = self.routine_store.get(result.routine_id)
        routine_name = routine.name if routine is not None else ""
        twitch = DryRunTwitchService()
        variable_registry = VariableRegistry()
        variable_registry.register(context_provider())
        variable_registry.register(
            runtime_provider(
                lambda: {
                    "category": self.live_context.get("stream.category", ""),
                    "channel": self.live_context.get("stream.channel", ""),
                    "title": self.live_context.get("stream.title", ""),
                    "viewer_count": self.live_context.get("stream.viewer_count"),
                    "game_id": self.live_context.get("stream.game_id", ""),
                    "connected": bool(self.live_context),
                },
                obs_connected=lambda: bool(self.live_context.get("obs.current_scene")),
                obs_scene=lambda: self.live_context.get("obs.current_scene", ""),
                hub_uptime=lambda: self.live_context.get("hub.uptime", "00:00:00"),
            )
        )
        task_registry = self._build_registry(twitch, variable_registry)
        automation = AutomationService(
            self.routine_store,
            task_registry,
            variable_registry=variable_registry,
        )
        execution = automation.publish_trigger(result.to_event())
        preview_context = {**result.context, **self.live_context}
        preview_context.update(variable_registry.context_values(preview_context))
        task_results = tuple(
            task_result
            for routine_result in execution.routine_results
            for task_result in routine_result.task_results
        )
        return TwitchCommandSimulation(
            outcome=result.outcome.value,
            invocation=result.invocation,
            trigger_id=result.trigger_id,
            routine_id=result.routine_id,
            routine_name=routine_name,
            context=preview_context,
            sent_messages=tuple(twitch.sent_messages),
            task_results=task_results,
            missing_variables=_missing_variables(
                routine.tasks if routine is not None else [],
                preview_context,
                self.live_context,
            ),
        )

    def _build_registry(
        self, twitch: DryRunTwitchService, variables: VariableRegistry
    ) -> TaskRegistry:
        registry = TaskRegistry()
        registry.register(
            SendTwitchChatMessageTask(twitch, variable_registry=variables)  # type: ignore[arg-type]
        )
        for task_type, label in {
            **TWITCH_TASK_LABELS,
            **CORE_TASK_LABELS,
            **OBS_TASK_LABELS,
        }.items():
            if task_type != SendTwitchChatMessageTask.task_type:
                registry.register(DryRunTaskHandler(task_type, label))
        return registry

def _render_config(config: Mapping[str, Any], context: Mapping[str, str]) -> str:
    parts: list[str] = []
    for key, value in config.items():
        if isinstance(value, str):
            rendered = SendTwitchChatMessageTask.render(value, context)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def _missing_variables(
    tasks: list[TaskDefinition],
    context: Mapping[str, str],
    live_context: Mapping[str, str],
) -> tuple[str, ...]:
    missing: set[str] = set()
    for task in tasks:
        for value in task.config.values():
            if not isinstance(value, str):
                continue
            for key in SendTwitchChatMessageTask.TEMPLATE_PATTERN.findall(value):
                if context.get(key, "--") in {"", "--"} and not live_context.get(key):
                    missing.add(key)
    return tuple(sorted(missing))
