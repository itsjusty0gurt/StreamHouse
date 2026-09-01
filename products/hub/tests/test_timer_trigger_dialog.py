import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_triggers import CoreAutomationTrigger
from products.hub.ui.automation_page import TimerTriggerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_fixed_timer_dialog_uses_decimal_value_and_supported_units() -> None:
    app = _app()
    dialog = TimerTriggerDialog()
    dialog.minimum_spin.setValue(1.5)
    dialog.minimum_unit.setCurrentIndex(dialog.minimum_unit.findData("minutes"))

    values = dialog.values()

    assert values["timer_mode"] == "fixed"
    assert values["timer_minimum"] == "1.5"
    assert values["timer_minimum_unit"] == "minutes"
    assert values["timer_maximum"] == ""
    assert [dialog.minimum_unit.itemData(index) for index in range(3)] == [
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

    assert not dialog.maximum_spin.parentWidget().isHidden()
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
