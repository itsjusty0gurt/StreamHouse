import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_triggers import CoreAutomationTrigger
from products.hub.automation.variable_registry import (
    CallbackVariableProvider,
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
    VariableRegistry,
)
from products.hub.ui.automation_page import IfTriggerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _registry() -> VariableRegistry:
    registry = VariableRegistry()
    definitions = (
        VariableDefinition(
            "custom.mode",
            "Mode",
            "Current mode",
            VariableDataType.TEXT,
            "Custom",
            "Custom",
        ),
        VariableDefinition(
            "user.id",
            "User ID",
            "Trigger user",
            VariableDataType.TEXT,
            "Context",
            "User",
            availability=VariableAvailability.CONTEXTUAL,
        ),
    )
    registry.register(
        CallbackVariableProvider(
            "Test", definitions, lambda _name, _context: (True, "BRB", "")
        )
    )
    return registry


def test_if_trigger_dialog_reuses_condition_builder_without_branches() -> None:
    _app()
    dialog = IfTriggerDialog(_registry())
    assert dialog.condition_builder is not None
    assert not hasattr(dialog, "then_tasks")
    assert not hasattr(dialog, "else_tasks")
    operators = {
        dialog.condition_builder.rows[0].operator_combo.itemData(index)
        for index in range(dialog.condition_builder.rows[0].operator_combo.count())
    }
    assert "user_in_group" not in operators
    assert dialog.firing_mode_combo.currentData() == "becomes_true"
    dialog.close()


def test_if_trigger_dialog_edits_conditions_mode_and_enabled_state() -> None:
    _app()
    trigger = CoreAutomationTrigger(
        "trigger",
        "routine",
        "if",
        enabled=False,
        condition_join="or",
        conditions=[
            {
                "left": "{custom.mode}",
                "operator": "equals",
                "right": "LIVE",
                "ignore_case": True,
                "group_id": "",
            }
        ],
        firing_mode="changes",
    )
    dialog = IfTriggerDialog(_registry(), trigger=trigger)
    values = dialog.values()
    assert values["condition_join"] == "or"
    assert values["conditions"][0]["left"] == "{custom.mode}"
    assert values["firing_mode"] == "changes"
    assert values["enabled"] is False
    dialog.close()


def test_if_trigger_picker_limits_choices_to_global_variables() -> None:
    _app()
    dialog = IfTriggerDialog(_registry())
    variable_filter = dialog.condition_builder.variable_filter
    definitions = {item.name: item for item in dialog.condition_builder.registry.definitions()}
    assert variable_filter(definitions["custom.mode"])
    assert not variable_filter(definitions["user.id"])
    dialog.close()
