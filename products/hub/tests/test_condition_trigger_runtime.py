import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from products.hub.automation.condition_trigger_runtime import CoreIfTriggerRuntime
from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_providers import CustomVariableProvider
from products.hub.automation.variable_registry import (
    CallbackVariableProvider,
    VariableDataType,
    VariableDefinition,
    VariableRegistry,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class MutableProvider:
    source = "Test"

    def __init__(self) -> None:
        self.values = {"custom.mode": "BRB", "module.state": "idle"}
        self.available = {"custom.mode": True, "module.state": True}
        self._definitions = tuple(
            VariableDefinition(
                name,
                name,
                "Test value",
                VariableDataType.TEXT,
                self.source,
                self.source,
            )
            for name in self.values
        )

    def definitions(self):
        return self._definitions

    def resolve(self, name, _context):
        definition = next(item for item in self._definitions if item.name == name)
        from products.hub.automation.variable_registry import VariableSnapshot

        return VariableSnapshot(
            definition,
            self.values[name],
            self.available[name],
            "Unavailable" if not self.available[name] else "",
        )

    def set_value(self, _name, _value):
        raise PermissionError


@pytest.fixture
def harness(tmp_path: Path):
    _app()
    routines = RoutineStore(tmp_path / "routines.json")
    store = CoreTriggerStore(tmp_path / "core.json", routines)
    registry = VariableRegistry()
    provider = MutableProvider()
    registry.register(provider)
    events = []
    runtime = CoreIfTriggerRuntime(
        store,
        registry,
        lambda event, mode: events.append((event, mode)),
    )
    runtime.start()
    yield routines, store, registry, provider, events, runtime
    runtime.stop()


def _condition(name="custom.mode", right="LIVE"):
    return {
        "left": "{" + name + "}",
        "operator": "equals",
        "right": right,
        "ignore_case": False,
        "group_id": "",
    }


@pytest.mark.parametrize(
    ("mode", "first", "second", "expected"),
    (
        ("becomes_true", "LIVE", "BRB", [True]),
        ("becomes_false", "LIVE", "BRB", [False]),
        ("changes", "LIVE", "BRB", [True, False]),
    ),
)
def test_edge_modes_fire_only_on_boolean_transitions(
    harness, mode, first, second, expected
) -> None:
    routines, store, registry, provider, events, runtime = harness
    routine = routines.add(mode)
    store.add_if(
        routine.routine_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode=mode,
    )
    assert events == []

    for value in (first, first, second, second):
        provider.values["custom.mode"] = value
        runtime.reevaluate(("custom.mode",))

    assert [item[0].context["core.if.result"] == "true" for item in events] == expected
    assert all(item[0].context["core.if.mode"] == mode for item in events)


def test_initial_true_and_false_only_establish_baselines(harness) -> None:
    routines, store, _registry, provider, events, _runtime = harness
    provider.values["custom.mode"] = "LIVE"
    live = routines.add("Initially true")
    store.add_if(
        live.routine_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode="becomes_true",
    )
    provider.values["custom.mode"] = "BRB"
    brb = routines.add("Initially false")
    store.add_if(
        brb.routine_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode="becomes_true",
    )
    assert events == []


def test_and_or_dependencies_and_unrelated_changes(harness) -> None:
    routines, store, _registry, provider, events, runtime = harness
    provider.values.update({"custom.mode": "BRB", "module.state": "idle"})
    and_routine = routines.add("AND")
    store.add_if(
        and_routine.routine_id,
        condition_join="and",
        conditions=[_condition(), _condition("module.state", "ready")],
        firing_mode="becomes_true",
    )
    or_routine = routines.add("OR")
    store.add_if(
        or_routine.routine_id,
        condition_join="or",
        conditions=[_condition(), _condition("module.state", "ready")],
        firing_mode="becomes_true",
    )

    provider.values["custom.mode"] = "LIVE"
    runtime.reevaluate(("unrelated.value",))
    assert events == []
    runtime.reevaluate(("custom.mode",))
    assert len(events) == 1
    provider.values["module.state"] = "ready"
    runtime.reevaluate(("module.state",))
    assert len(events) == 2


def test_disable_enable_and_missing_variables_establish_clean_baseline(harness) -> None:
    routines, store, _registry, provider, events, runtime = harness
    routine = routines.add("Lifecycle")
    trigger = store.add_if(
        routine.routine_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode="changes",
        enabled=False,
    )
    provider.values["custom.mode"] = "LIVE"
    runtime.reevaluate(("custom.mode",))
    assert events == []
    store.update_if(
        trigger.trigger_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode="changes",
        enabled=True,
    )
    assert events == []
    provider.available["custom.mode"] = False
    runtime.reevaluate(("custom.mode",))
    provider.available["custom.mode"] = True
    runtime.reevaluate(("custom.mode",))
    assert events == []
    provider.values["custom.mode"] = "BRB"
    runtime.reevaluate(("custom.mode",))
    assert len(events) == 1


def test_registry_notifications_are_queued_and_self_changes_do_not_repeat(harness) -> None:
    routines, store, registry, provider, events, _runtime = harness
    routine = routines.add("Queued")
    store.add_if(
        routine.routine_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode="becomes_true",
    )
    provider.values["custom.mode"] = "LIVE"
    registry.notify_changed(("custom.mode",))
    _app().processEvents()
    _app().processEvents()
    assert len(events) == 1
    registry.notify_changed(("custom.mode",))
    _app().processEvents()
    _app().processEvents()
    assert len(events) == 1


def test_custom_variable_store_change_reaches_trigger_runtime(tmp_path: Path) -> None:
    _app()
    routines = RoutineStore(tmp_path / "routines.json")
    store = CoreTriggerStore(tmp_path / "core.json", routines)
    variables = CustomVariableStore(tmp_path / "variables.json")
    variables.load_for_startup()
    variables.set("global", "mode", "BRB")
    registry = VariableRegistry()
    registry.register(CustomVariableProvider(variables))
    callback = lambda name: registry.notify_changed((f"custom.{name}",))
    variables.subscribe_changes(callback)
    events = []
    runtime = CoreIfTriggerRuntime(
        store, registry, lambda event, mode: events.append((event, mode))
    )
    routine = routines.add("Custom mode")
    store.add_if(
        routine.routine_id,
        condition_join="and",
        conditions=[_condition()],
        firing_mode="becomes_true",
    )
    runtime.start()
    try:
        variables.set("global", "mode", "LIVE")
        _app().processEvents()
        _app().processEvents()
        assert len(events) == 1
    finally:
        runtime.stop()
        variables.unsubscribe_changes(callback)
