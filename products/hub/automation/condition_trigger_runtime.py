from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from products.hub.automation.conditions import (
    condition_dependencies,
    evaluate_condition_set,
)
from products.hub.automation.core_triggers import (
    IF_TRIGGER_FIRING_MODES,
    CoreAutomationTrigger,
    CoreTriggerStore,
)
from products.hub.automation.models import TriggerEvent
from products.hub.automation.variable_registry import VariableRegistry
from shared.streamhouse_runtime.logger import Logger


class CoreIfTriggerRuntime(QObject):
    """Edge-detect Core If triggers from provider-owned Variable updates."""

    _changes_received = Signal(object)

    def __init__(
        self,
        store: CoreTriggerStore,
        variables: VariableRegistry,
        fire: Callable[[TriggerEvent, str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.variables = variables
        self.fire = fire
        self._states: dict[str, bool] = {}
        self._signatures: dict[str, tuple[object, ...]] = {}
        self._dependencies: dict[str, frozenset[str]] = {}
        self._errors: dict[str, str] = {}
        self._pending_names: set[str] = set()
        self._all_pending = False
        self._processing_scheduled = False
        self._started = False
        self._changes_received.connect(
            self._collect_changes,
            Qt.ConnectionType.QueuedConnection,
        )

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.store.subscribe_changes(self.synchronize)
        self.variables.subscribe_changes(self._variable_values_changed)
        self.synchronize()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self.store.unsubscribe_changes(self.synchronize)
        self.variables.unsubscribe_changes(self._variable_values_changed)
        self._states.clear()
        self._signatures.clear()
        self._dependencies.clear()
        self._errors.clear()
        self._pending_names.clear()
        self._all_pending = False
        self._processing_scheduled = False

    def synchronize(self) -> None:
        """Rebuild dependency indexes and baseline new or edited triggers."""

        active_ids: set[str] = set()
        for trigger in self.store.triggers:
            if trigger.event_type != "if" or not trigger.enabled:
                continue
            active_ids.add(trigger.trigger_id)
            signature = self._signature(trigger)
            dependencies = condition_dependencies(trigger.condition_config())
            self._dependencies[trigger.trigger_id] = dependencies
            if self._signatures.get(trigger.trigger_id) == signature:
                continue
            self._signatures[trigger.trigger_id] = signature
            self._states.pop(trigger.trigger_id, None)
            self._errors.pop(trigger.trigger_id, None)
            self._evaluate(trigger, fire=False)
        for trigger_id in set(self._signatures) - active_ids:
            self._signatures.pop(trigger_id, None)
            self._dependencies.pop(trigger_id, None)
            self._states.pop(trigger_id, None)
            self._errors.pop(trigger_id, None)

    def reset_baselines(self) -> None:
        """Forget runtime-only states after restored durable data is reloaded."""

        self._states.clear()
        self._signatures.clear()
        self._dependencies.clear()
        self._errors.clear()
        self.synchronize()

    def reevaluate(self, changed_names: Iterable[str] | None = None) -> None:
        """Evaluate affected triggers immediately; useful for deterministic tests."""

        changed = frozenset(str(name).strip().casefold() for name in changed_names or ())
        evaluate_all = changed_names is None or not changed
        for trigger in tuple(self.store.triggers):
            if trigger.event_type != "if" or not trigger.enabled:
                continue
            dependencies = self._dependencies.get(trigger.trigger_id, frozenset())
            if not evaluate_all and not dependencies.intersection(changed):
                continue
            self._evaluate(trigger, fire=True)

    def _variable_values_changed(self, names: frozenset[str]) -> None:
        if self._started:
            self._changes_received.emit(names)

    @Slot(object)
    def _collect_changes(self, names: object) -> None:
        if not self._started:
            return
        changed = frozenset(names) if isinstance(names, (set, frozenset)) else frozenset()
        if changed:
            self._pending_names.update(str(name) for name in changed)
        else:
            self._all_pending = True
        if self._processing_scheduled:
            return
        self._processing_scheduled = True
        QTimer.singleShot(0, self, self._process_pending_changes)

    @Slot()
    def _process_pending_changes(self) -> None:
        self._processing_scheduled = False
        if not self._started:
            return
        changed = None if self._all_pending else frozenset(self._pending_names)
        self._pending_names.clear()
        self._all_pending = False
        self.reevaluate(changed)

    def _evaluate(self, trigger: CoreAutomationTrigger, *, fire: bool) -> None:
        try:
            context = self._context(trigger)
            result = evaluate_condition_set(trigger.condition_config(), context)
        except (KeyError, TypeError, ValueError) as error:
            message = str(error)
            if self._errors.get(trigger.trigger_id) != message:
                Logger.warning(
                    f'Core If trigger "{trigger.trigger_id}" could not evaluate: '
                    f"{message}",
                    source="AUTOMATION",
                )
                self._errors[trigger.trigger_id] = message
            return
        self._errors.pop(trigger.trigger_id, None)
        previous = self._states.get(trigger.trigger_id)
        self._states[trigger.trigger_id] = result
        if previous is None or not fire or previous == result:
            return
        should_fire = (
            trigger.firing_mode == "changes"
            or trigger.firing_mode == "becomes_true" and result
            or trigger.firing_mode == "becomes_false" and not result
        )
        if not should_fire:
            return
        mode = IF_TRIGGER_FIRING_MODES[trigger.firing_mode]
        event = TriggerEvent(
            trigger_id=trigger.trigger_id,
            service="core",
            trigger_type="if",
            context={
                **context,
                "event": "If",
                "event_type": "if",
                "core.if.result": "true" if result else "false",
                "core.if.mode": trigger.firing_mode,
            },
        )
        self.fire(event, mode)

    def _context(self, trigger: CoreAutomationTrigger) -> dict[str, str]:
        context: dict[str, str] = {}
        for name in self._dependencies.get(trigger.trigger_id, frozenset()):
            snapshot = self.variables.resolve(name)
            if snapshot is None or not snapshot.available:
                raise ValueError(f'Variable "{{{name}}}" is not available for this If.')
            context[name] = snapshot.display_value
        return context

    @staticmethod
    def _signature(trigger: CoreAutomationTrigger) -> tuple[object, ...]:
        conditions = tuple(
            tuple(sorted((str(key), repr(value)) for key, value in condition.items()))
            for condition in trigger.conditions
        )
        return (
            trigger.enabled,
            trigger.condition_join,
            conditions,
            trigger.firing_mode,
        )
