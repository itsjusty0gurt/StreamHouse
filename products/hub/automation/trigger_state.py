from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping

from products.hub.automation.control_tasks import _enabled_for, _result
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.modules.contributions import TriggerProviderRegistry


class TriggerState:
    """Resolve stable routine/trigger pairs against authoritative provider stores."""

    def __init__(
        self, routines: RoutineStore, providers: TriggerProviderRegistry,
        local_stores: tuple[tuple[str, object, Mapping[str, str]], ...],
        changed: Callable[[], None],
        *,
        local_available: Callable[[str], bool] | None = None,
    ) -> None:
        self.routines = routines
        self.providers = providers
        self.local_stores = local_stores
        self.changed = changed
        self.local_available = local_available or (lambda _provider: True)

    def _targets(self, routine_id: str) -> Iterator[tuple[str, object, object]]:
        routine = self.routines.get(routine_id)
        if routine is None:
            return
        providers = (*(item for item in self.local_stores if self.local_available(item[0])), *(
            (provider.display_name, provider.store, provider.event_types)
            for provider in self.providers.contributions()
            if provider.current_availability().available
        ))
        for provider, store, event_types in providers:
            setter = getattr(store, "set_enabled", None)
            if setter is None:
                continue
            for trigger_id in routine.trigger_ids:
                target = store.get(trigger_id)
                if target is None or target.routine_id != routine_id:
                    continue
                event_type = getattr(target, "event_type", "")
                label = event_types.get(event_type, event_type)
                if event_type == "custom_event":
                    label += f" — {target.event_name}"
                elif not event_type:
                    label = f"!{target.name}"
                yield f"{provider} — {label}", target, setter

    def options(self, routine_id: str) -> tuple[tuple[str, str], ...]:
        return tuple((label, trigger.trigger_id) for label, trigger, _ in self._targets(routine_id))

    def set_state(self, routine_id: str, trigger_id: str, action: str) -> str:
        matches = [
            (label, target, setter) for label, target, setter in self._targets(routine_id)
            if target.trigger_id == trigger_id
        ]
        if len(matches) != 1:
            raise ValueError("The selected trigger is missing, unavailable, or not linked to this routine.")
        label, target, setter = matches[0]
        if action not in {"enable", "disable", "toggle"}:
            raise ValueError("Choose Enable, Disable, or Toggle.")
        enabled = _enabled_for(action, target.enabled)
        if target.enabled != enabled:
            if not setter(trigger_id, enabled):
                raise ValueError("The selected trigger is no longer available.")
            self.changed()
        return f'{"Enabled" if enabled else "Disabled"} {label}.'


class SetTriggerStateTask:
    task_type = "core.set_trigger_state"

    def __init__(self, state: TriggerState) -> None:
        self.state = state

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            if task.config.get("requires_relink"):
                raise ValueError("Relink the imported task to a trigger before running it.")
            detail = self.state.set_state(
                str(task.config.get("routine_id", "")).strip(),
                str(task.config.get("trigger_id", "")).strip(),
                str(task.config.get("action", "toggle")).strip().casefold(),
            )
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))
        return _result(task, True, detail)
