from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QPushButton
from unittest.mock import patch

from products.hub.automation.routines import RoutineStore
from products.hub.automation.models import TaskDefinition
from products.hub.counters.models import CounterDefinition
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.ui.automation_page import CounterScopesEditor, TaskEditorDialog
from products.hub.ui.counters_page import CounterDefinitionDialog, CountersPage
from products.hub.ui.page_header import PageHeader


class CountersPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = TemporaryDirectory(); root = Path(self.temp.name)
        self.service = CounterService(CounterStore(root / "counters"))
        self.service.create_counter(CounterDefinition("farts", "Farts", "fart", "farts"))
        self.routines = RoutineStore(root / "routines.json")
        self.page = CountersPage(self.service, lambda: "stream-1", self.routines)

    def tearDown(self) -> None:
        self.page.deleteLater(); self.application.processEvents(); self.temp.cleanup()

    def test_list_search_manual_adjustment_and_viewer_removal_are_scoped(self) -> None:
        self.assertEqual(self.page.page_header.title_label.text(), "Counters")
        self.assertIs(
            self.page.new_button.parentWidget(),
            self.page.page_header.action_widget,
        )
        self.assertEqual(self.page.list.count(), 1)
        self.page.plus.click()
        self.assertEqual(self.service.get_values("farts").channel_total, 1)
        self.service.update_values("farts", 2, ("viewer_total",), user_id="111", display_name="Steve")
        self.page.refresh(); self.assertEqual(self.page.viewer_table.rowCount(), 1)
        self.page.viewer_search.setText("nobody"); self.assertEqual(self.page.viewer_table.rowCount(), 0)
        self.page.search.setText("bonks"); self.assertEqual(self.page.list.count(), 0)

    def test_page_created_while_live_uses_current_stream_values(self) -> None:
        self.service.update_values(
            "farts",
            7,
            ("channel_total", "stream_total"),
            stream_id="stream-1",
        )

        page = CountersPage(self.service, lambda: "stream-1", self.routines)
        try:
            self.assertIn("7 farts", page.list.item(0).text())
            self.assertIn("Current Twitch stream: 7 farts", page.summary.text())
            self.assertTrue(page.set_stream.isEnabled())
        finally:
            page.deleteLater()

    def test_service_reports_successful_value_and_manual_changes(self) -> None:
        changed: list[str] = []
        self.service.subscribe_changes(changed.append)

        self.service.update_values(
            "farts",
            1,
            ("channel_total", "stream_total"),
            stream_id="stream-1",
        )
        self.service.set_value("farts", "channel_total", 5)

        self.assertEqual(changed, ["farts", "farts"])

    def test_header_matches_shared_page_proportions(self) -> None:
        margins = self.page.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (12, 10, 12, 12),
        )
        self.assertEqual(
            self.page.page_header.subtitle_label.text(),
            "Track shared, stream, and viewer totals.",
        )

        reference = PageHeader(
            "Automation",
            "Manage routines, triggers, tasks, queues, and run history.",
        )
        reference.add_action(QPushButton("New Routine"))
        try:
            available_width = 780
            self.page.page_header.set_compact(False)
            reference.set_compact(False)
            self.assertLessEqual(
                self.page.page_header.heightForWidth(available_width),
                reference.heightForWidth(available_width),
            )
            self.assertEqual(
                self.page.page_header.title_label.font().pointSize(),
                reference.title_label.font().pointSize(),
            )
            self.assertEqual(
                self.page.page_header.title_label.font().bold(),
                reference.title_label.font().bold(),
            )
        finally:
            reference.deleteLater()

    def test_new_counter_tasks_default_to_every_configured_scope(self) -> None:
        for task_type in (
            "counter.increase",
            "counter.decrease",
            "counter.set_value",
            "counter.reset",
        ):
            with self.subTest(task_type=task_type):
                dialog = TaskEditorDialog(
                    task_type,
                    counter_service=self.service,
                    variables={"user_id": "111"},
                )
                fields = dialog.field_widgets[task_type]
                self.assertIsInstance(fields["counter_id"], QComboBox)
                self.assertIsInstance(fields["scopes"], CounterScopesEditor)
                fields["counter_id"].setCurrentIndex(
                    fields["counter_id"].findData("farts")
                )
                values = dialog.values()
                self.assertEqual(values["config"]["counter_id"], "farts")
                self.assertEqual(
                    values["config"]["scopes"],
                    ["viewer_total", "stream_total", "channel_total"],
                )
                self.assertNotIn("scope", values["config"])
                dialog.deleteLater()

    def test_counter_scope_choices_hide_untracked_and_allow_subset(self) -> None:
        dialog = TaskEditorDialog(
            "counter.increase",
            counter_service=self.service,
            variables={"user_id": "111"},
        )
        fields = dialog.field_widgets["counter.increase"]
        fields["counter_id"].setCurrentIndex(fields["counter_id"].findData("farts"))
        scopes = fields["scopes"]
        self.assertIsInstance(scopes, CounterScopesEditor)
        self.assertTrue(scopes.checkboxes["viewer_stream_total"].isHidden())
        scopes.checkboxes["stream_total"].setChecked(False)
        scopes.checkboxes["channel_total"].setChecked(False)
        fields["amount"].setText("{command.data}")

        values = dialog.values()

        self.assertEqual(values["config"]["scopes"], ["viewer_total"])
        self.assertEqual(values["config"]["amount"], "{command.data}")
        dialog.deleteLater()

    def test_counter_change_defaults_new_task_but_does_not_broaden_existing_task(self) -> None:
        self.service.create_counter(
            CounterDefinition(
                "stream_only",
                "Stream Only",
                "",
                "",
                track_channel_total=False,
                track_stream_total=True,
                track_viewer_total=False,
                track_viewer_stream_total=True,
            )
        )
        new_dialog = TaskEditorDialog("counter.reset", counter_service=self.service)
        new_fields = new_dialog.field_widgets["counter.reset"]
        new_fields["counter_id"].setCurrentIndex(
            new_fields["counter_id"].findData("farts")
        )
        new_fields["scopes"].checkboxes["viewer_total"].setChecked(False)
        new_fields["counter_id"].setCurrentIndex(
            new_fields["counter_id"].findData("stream_only")
        )
        self.assertEqual(
            new_fields["scopes"].value(),
            ["viewer_stream_total", "stream_total"],
        )

        existing = TaskDefinition(
            "task-1",
            "counter.reset",
            "Reset",
            {"counter_id": "farts", "scope": "stream_total"},
        )
        edit_dialog = TaskEditorDialog(
            "counter.reset",
            task=existing,
            counter_service=self.service,
        )
        edit_fields = edit_dialog.field_widgets["counter.reset"]
        self.assertEqual(edit_fields["scopes"].value(), ["stream_total"])
        edit_fields["counter_id"].setCurrentIndex(
            edit_fields["counter_id"].findData("stream_only")
        )
        self.assertEqual(edit_fields["scopes"].value(), ["stream_total"])
        new_dialog.deleteLater()
        edit_dialog.deleteLater()

    def test_counter_task_rejects_zero_selected_scopes(self) -> None:
        dialog = TaskEditorDialog("counter.increase", counter_service=self.service)
        fields = dialog.field_widgets["counter.increase"]
        fields["counter_id"].setCurrentIndex(fields["counter_id"].findData("farts"))
        for checkbox in fields["scopes"].checkboxes.values():
            checkbox.setChecked(False)
        with self.assertRaisesRegex(ValueError, "at least one counter scope"):
            dialog.values()
        dialog.deleteLater()

    def test_counter_task_keeps_stable_id_after_counter_rename(self) -> None:
        dialog = TaskEditorDialog("counter.set_value", counter_service=self.service)
        fields = dialog.field_widgets["counter.set_value"]
        fields["counter_id"].setCurrentIndex(fields["counter_id"].findData("farts"))
        self.service.update_counter("farts", display_name="Renamed Counter")

        values = dialog.values()

        self.assertEqual(values["config"]["counter_id"], "farts")
        dialog.deleteLater()

    def test_fresh_page_shows_empty_state_without_creating_storage(self) -> None:
        root = Path(self.temp.name) / "fresh"
        service = CounterService(CounterStore(root / "counters"))
        page = CountersPage(service, lambda: "", self.routines)
        try:
            self.assertTrue(page.empty_state.isVisibleTo(page))
            self.assertFalse(page.split.isVisibleTo(page))
            self.assertEqual(service.list_counters(), ())
            self.assertFalse((root / "counters").exists())
        finally:
            page.deleteLater()

    def test_inline_create_cancel_does_not_create_counter(self) -> None:
        fresh = CounterService(CounterStore(Path(self.temp.name) / "inline" / "counters"))
        dialog = TaskEditorDialog("counter.increase", counter_service=fresh)
        combo = dialog.field_widgets["counter.increase"]["counter_id"]
        combo.setCurrentIndex(combo.findData("__create__"))
        with patch("products.hub.ui.automation_page.CounterDefinitionDialog.exec", return_value=0):
            dialog._create_inline_counter(combo)
        self.assertEqual(fresh.list_counters(), ())
        self.assertFalse((Path(self.temp.name) / "inline" / "counters").exists())
        dialog.deleteLater()

    def test_deleted_reference_loads_as_missing_counter(self) -> None:
        task = TaskDefinition("task-1", "counter.set_value", "Set", {"counter_id": "deleted_counter", "scope": "channel_total", "value": "1"})
        dialog = TaskEditorDialog("counter.set_value", task=task, counter_service=self.service)
        combo = dialog.field_widgets["counter.set_value"]["counter_id"]
        self.assertEqual(combo.currentData(), "deleted_counter")
        self.assertIn("Missing Counter", combo.currentText())
        dialog.deleteLater()

    def test_definition_dialog_configures_decimal_reset_and_precision(self) -> None:
        dialog = CounterDefinitionDialog(self.service)
        dialog.name.setText("Coffee Drank")
        dialog.numeric_type.setCurrentIndex(dialog.numeric_type.findData("decimal"))
        dialog.reset_value.setValue(1.25)
        dialog.display_precision.setValue(1)
        dialog.singular.setText("cup")
        dialog.plural.setText("cups")
        definition = dialog.values()
        self.assertEqual(definition.counter_id, "coffee_drank")
        self.assertEqual(definition.numeric_type, "decimal")
        self.assertEqual(str(definition.reset_value), "1.25")
        self.assertEqual(definition.display_precision, 1)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
