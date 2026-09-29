import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.timer_scheduler import AutomationTimerScheduler
from products.hub.ui.timers_page import RoutineTargetDialog, TimersPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _harness():
    app = _app()
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    routines = RoutineStore(root / "routines.json")
    triggers = CoreTriggerStore(root / "core.json", routines)
    clock = [0.0]
    scheduler = AutomationTimerScheduler(
        triggers,
        lambda _event, _description: None,
        clock=lambda: clock[0],
        auto_arm=False,
    )
    scheduler.start()
    page = TimersPage(routines, triggers, scheduler)
    return app, temporary, routines, triggers, scheduler, clock, page


def _close(app, temporary, scheduler, page) -> None:
    page.shutdown()
    page.close()
    scheduler.shutdown()
    app.processEvents()
    temporary.cleanup()


def test_timer_cards_use_store_format_and_authoritative_runtime_status() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        routine = routines.add("Discord Promotion")
        trigger = triggers.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="10",
            timer_minimum_unit="minutes",
        )
        app.processEvents()
        card = page.cards[trigger.trigger_id]

        assert card.title_label.text() == "Discord Promotion"
        assert card.description_label.text() == "Every 10 minutes"
        assert card.details_label.text() == "Recurring"
        assert card.used_by_label.text() == "Used by: Discord Promotion"
        assert card.remaining_label.text() == "Next run in 10:00"

        clock[0] = 73
        page.refresh_countdowns()
        assert page.cards[trigger.trigger_id] is card
        assert card.remaining_label.text() == "Next run in 08:47"
    finally:
        _close(app, temporary, scheduler, page)


def test_countdown_stress_and_responsive_reflow_preserve_card_identity() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        for index in range(20):
            routine = routines.add(f"Routine {index}")
            triggers.add_timer(
                routine.routine_id,
                timer_mode="fixed",
                timer_minimum="2",
                timer_minimum_unit="hours",
            )
        app.processEvents()
        identities = {key: id(card) for key, card in page.cards.items()}

        for tick in range(2_000):
            clock[0] = tick / 10
            page.refresh_countdowns()
        assert {key: id(card) for key, card in page.cards.items()} == identities

        assert TimersPage.columns_for_width(320) == 1
        assert TimersPage.columns_for_width(680) >= 2
        assert TimersPage.columns_for_width(1_020) >= 3
        page.scroll_area.resize(1_200, 700)
        page._reflow(force=True)
        assert {key: id(card) for key, card in page.cards.items()} == identities
        assert len(page.cards) == 20
    finally:
        _close(app, temporary, scheduler, page)


def test_disable_enable_delete_and_open_routine_use_stable_ids() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        routine = routines.add("Hydration")
        trigger = triggers.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="5",
            timer_minimum_unit="minutes",
        )
        app.processEvents()
        opened = []
        page.open_routine_requested.connect(opened.append)
        card = page.cards[trigger.trigger_id]
        card.open_button.click()
        assert opened == [routine.routine_id]

        page.toggle_timer(trigger.trigger_id)
        assert not triggers.get(trigger.trigger_id).enabled
        assert scheduler.status(trigger.trigger_id).scheduled is False
        assert card.remaining_label.text() == "Disabled"
        page.toggle_timer(trigger.trigger_id)
        assert triggers.get(trigger.trigger_id).enabled
        assert scheduler.status(trigger.trigger_id).scheduled is True

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page.delete_timer(trigger.trigger_id)
        app.processEvents()
        assert triggers.get(trigger.trigger_id) is None
        assert trigger.trigger_id not in routines.get(routine.routine_id).trigger_ids
        assert trigger.trigger_id not in page.cards
    finally:
        _close(app, temporary, scheduler, page)


def test_edit_action_updates_same_trigger_and_card_through_shared_editor() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        routine = routines.add("Reminder")
        trigger = triggers.add_timer(
            routine.routine_id,
            timer_mode="fixed",
            timer_minimum="5",
            timer_minimum_unit="minutes",
        )
        app.processEvents()
        card = page.cards[trigger.trigger_id]
        with (
            patch(
                "products.hub.ui.timers_page.TimerTriggerDialog.exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch(
                "products.hub.ui.timers_page.TimerTriggerDialog.values",
                return_value={
                    "timer_mode": "fixed",
                    "timer_minimum": "30",
                    "timer_minimum_unit": "seconds",
                    "timer_maximum": "",
                    "timer_maximum_unit": "seconds",
                    "enabled": True,
                },
            ),
        ):
            card.edit_button.click()
        app.processEvents()

        assert triggers.get(trigger.trigger_id).timer_minimum == "30"
        assert page.cards[trigger.trigger_id] is card
        assert card.description_label.text() == "Every 30 seconds"
    finally:
        _close(app, temporary, scheduler, page)


def test_add_timer_reuses_existing_dialog_and_core_trigger_store() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        routine = routines.add("Social Rotation")
        with (
            patch.object(
                RoutineTargetDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch.object(RoutineTargetDialog, "target", return_value=routine.routine_id),
            patch(
                "products.hub.ui.timers_page.TimerTriggerDialog.exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch(
                "products.hub.ui.timers_page.TimerTriggerDialog.values",
                return_value={
                    "timer_mode": "random",
                    "timer_minimum": "5",
                    "timer_minimum_unit": "minutes",
                    "timer_maximum": "15",
                    "timer_maximum_unit": "minutes",
                    "enabled": True,
                },
            ),
        ):
            page.add_timer()
        app.processEvents()

        timers = [item for item in triggers.triggers if item.event_type == "timer"]
        assert len(timers) == 1
        assert timers[0].routine_id == routine.routine_id
        assert timers[0].trigger_id in routines.get(routine.routine_id).trigger_ids
        assert page.cards[timers[0].trigger_id].description_label.text() == (
            "Random: 5–15 minutes"
        )
    finally:
        _close(app, temporary, scheduler, page)


def test_add_timer_can_use_existing_new_routine_flow_callback() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        created = []

        def create_routine():
            routine = routines.add("New Timer Routine")
            created.append(routine.routine_id)
            return routine

        page.create_routine = create_routine
        with (
            patch.object(
                RoutineTargetDialog,
                "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch.object(
                RoutineTargetDialog,
                "target",
                return_value=RoutineTargetDialog.CREATE_NEW,
            ),
            patch(
                "products.hub.ui.timers_page.TimerTriggerDialog.exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch(
                "products.hub.ui.timers_page.TimerTriggerDialog.values",
                return_value={
                    "timer_mode": "fixed",
                    "timer_minimum": "10",
                    "timer_minimum_unit": "minutes",
                    "timer_maximum": "",
                    "timer_maximum_unit": "seconds",
                    "enabled": True,
                },
            ),
        ):
            page.add_timer()
        app.processEvents()

        assert len(created) == 1
        timer = next(item for item in triggers.triggers if item.event_type == "timer")
        assert timer.routine_id == created[0]
    finally:
        _close(app, temporary, scheduler, page)


def test_filtering_and_routine_rename_update_cards_without_recreation() -> None:
    app, temporary, routines, triggers, scheduler, clock, page = _harness()
    try:
        first = routines.add("First Routine")
        second = routines.add("Second Routine")
        first_timer = triggers.add_timer(
            first.routine_id,
            timer_mode="fixed",
            timer_minimum="5",
            timer_minimum_unit="seconds",
        )
        second_timer = triggers.add_timer(
            second.routine_id,
            timer_mode="fixed",
            timer_minimum="10",
            timer_minimum_unit="seconds",
            enabled=False,
        )
        app.processEvents()
        first_card = page.cards[first_timer.trigger_id]
        second_card = page.cards[second_timer.trigger_id]

        page.search_edit.setText("second routine")
        assert first_card.isHidden()
        assert not second_card.isHidden()
        page.search_edit.clear()
        page.filter_combo.setCurrentIndex(page.filter_combo.findData("enabled"))
        assert not first_card.isHidden()
        assert second_card.isHidden()

        routines.update(first.routine_id, name="Renamed Routine")
        page.refresh()
        assert page.cards[first_timer.trigger_id] is first_card
        assert first_card.title_label.text() == "Renamed Routine"
    finally:
        _close(app, temporary, scheduler, page)
