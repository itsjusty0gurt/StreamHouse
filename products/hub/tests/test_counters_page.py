from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox
from unittest.mock import patch

from products.hub.automation.routines import RoutineStore
from products.hub.automation.models import TaskDefinition
from products.hub.counters.models import CounterDefinition
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.ui.automation_page import TaskEditorDialog
from products.hub.ui.counters_page import CounterDefinitionDialog, CountersPage


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
        self.assertEqual(self.page.list.count(), 1)
        self.page.plus.click()
        self.assertEqual(self.service.get_values("farts").channel_total, 1)
        self.service.update_values("farts", 2, ("viewer_total",), user_id="111", display_name="Steve")
        self.page.refresh(); self.assertEqual(self.page.viewer_table.rowCount(), 1)
        self.page.viewer_search.setText("nobody"); self.assertEqual(self.page.viewer_table.rowCount(), 0)
        self.page.search.setText("bonks"); self.assertEqual(self.page.list.count(), 0)

    def test_task_editor_counter_dropdown_scope_and_variable_amount(self) -> None:
        dialog = TaskEditorDialog("counter.increase", counter_service=self.service, variables={"user_id": "111"})
        fields = dialog.field_widgets["counter.increase"]
        self.assertIsInstance(fields["counter_id"], QComboBox)
        fields["counter_id"].setCurrentIndex(fields["counter_id"].findData("farts"))
        fields["amount"].setText("{command.data}")
        values = dialog.values()
        self.assertEqual(values["config"]["counter_id"], "farts")
        self.assertEqual(values["config"]["scope"], "channel_total")
        self.assertEqual(values["config"]["amount"], "{command.data}")
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
