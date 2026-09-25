import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_triggers import CoreAutomationTrigger
from products.hub.ui.automation_page import TimerTriggerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("value", "unit"),
    (
        ("500", "milliseconds"),
        ("10", "seconds"),
        ("5", "minutes"),
        ("2", "hours"),
        ("1.5", "seconds"),
    ),
)
def test_exact_timer_dialog_uses_text_value_and_supported_units(
    value: str,
    unit: str,
) -> None:
    app = _app()
    dialog = TimerTriggerDialog()
    dialog.minimum_edit.setText(value)
    dialog.minimum_unit.setCurrentIndex(dialog.minimum_unit.findData(unit))

    values = dialog.values()

    assert values["timer_mode"] == "fixed"
    assert values["timer_minimum"] == value
    assert values["timer_minimum_unit"] == unit
    assert values["timer_maximum"] == ""
    assert [dialog.minimum_unit.itemData(index) for index in range(4)] == [
        "milliseconds",
        "seconds",
        "minutes",
        "hours",
    ]
    dialog.close()
    app.processEvents()


def test_random_timer_dialog_restores_persisted_range() -> None:
    app = _app()
    trigger = CoreAutomationTrigger(
        "trigger-1",
        "routine-1",
        "timer",
        timer_mode="random",
        timer_minimum="30",
        timer_minimum_unit="minutes",
        timer_maximum="60",
        timer_maximum_unit="minutes",
    )
    dialog = TimerTriggerDialog(trigger=trigger)

    assert dialog.random_radio.isChecked()
    assert not dialog.maximum_edit.isHidden()
    assert dialog.values() == {
        "timer_mode": "random",
        "timer_minimum": "30",
        "timer_minimum_unit": "minutes",
        "timer_maximum": "60",
        "timer_maximum_unit": "minutes",
        "enabled": True,
    }
    dialog.close()
    app.processEvents()


def test_random_timer_dialog_supports_independent_units() -> None:
    app = _app()
    dialog = TimerTriggerDialog()
    dialog.random_radio.setChecked(True)
    dialog.minimum_edit.setText("500")
    dialog.minimum_unit.setCurrentIndex(
        dialog.minimum_unit.findData("milliseconds")
    )
    dialog.maximum_edit.setText("2")
    dialog.maximum_unit.setCurrentIndex(dialog.maximum_unit.findData("seconds"))

    assert dialog.values()["timer_minimum_unit"] == "milliseconds"
    assert dialog.values()["timer_maximum_unit"] == "seconds"
    assert not dialog.maximum_edit.isHidden()
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("minimum", "minimum_unit", "maximum", "maximum_unit", "message"),
    (
        ("", "seconds", "", "seconds", "positive numbers"),
        ("text", "seconds", "", "seconds", "positive numbers"),
        ("0", "seconds", "", "seconds", "positive finite"),
        ("-1", "seconds", "", "seconds", "positive finite"),
        ("99", "milliseconds", "", "seconds", "at least 100 milliseconds"),
        ("0.0001", "seconds", "", "seconds", "whole millisecond"),
        ("2", "minutes", "30", "seconds", "must not exceed"),
    ),
)
def test_timer_dialog_rejects_invalid_values(
    minimum: str,
    minimum_unit: str,
    maximum: str,
    maximum_unit: str,
    message: str,
) -> None:
    app = _app()
    dialog = TimerTriggerDialog()
    dialog.minimum_edit.setText(minimum)
    dialog.minimum_unit.setCurrentIndex(
        dialog.minimum_unit.findData(minimum_unit)
    )
    if maximum:
        dialog.random_radio.setChecked(True)
        dialog.maximum_edit.setText(maximum)
        dialog.maximum_unit.setCurrentIndex(
            dialog.maximum_unit.findData(maximum_unit)
        )

    with patch(
        "products.hub.ui.automation_page.QMessageBox.warning"
    ) as warning:
        dialog.accept()

    assert warning.call_count == 1
    assert message in str(warning.call_args.args[2])
    dialog.close()
    app.processEvents()


def test_random_timer_dialog_rejects_blank_maximum() -> None:
    app = _app()
    dialog = TimerTriggerDialog()
    dialog.random_radio.setChecked(True)
    dialog.minimum_edit.setText("5")
    dialog.maximum_edit.clear()

    with patch(
        "products.hub.ui.automation_page.QMessageBox.warning"
    ) as warning:
        dialog.accept()

    assert warning.call_count == 1
    assert "positive numbers" in str(warning.call_args.args[2])
    dialog.close()
    app.processEvents()
