from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox

from products.hub.automation.routines import RoutineStore
from products.hub.counters.models import CounterDefinition
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.ui.automation_page import TaskEditorDialog
from products.hub.ui.counters_page import CountersPage


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

    def test_task_editor_counter_dropdown_scopes_and_generated_prefix(self) -> None:
        dialog = TaskEditorDialog("counter.update", counter_service=self.service)
        fields = dialog.field_widgets["counter.update"]
        self.assertIsInstance(fields["counter_id"], QComboBox)
        fields["counter_id"].setCurrentIndex(fields["counter_id"].findData("farts"))
        values = dialog.values()
        self.assertEqual(values["config"]["counter_id"], "farts")
        self.assertEqual(values["config"]["output_prefix"], "farts")
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
