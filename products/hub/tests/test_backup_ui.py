from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.core.backup import (
    BackupComponent,
    BackupInspection,
    BackupPreset,
)
from products.hub.ui.backup_dialogs import (
    BackupSelectionDialog,
    RestoreSelectionDialog,
)


class BackupDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_custom_counter_values_lock_required_definitions(self) -> None:
        dialog = BackupSelectionDialog()
        dialog.preset_combo.setCurrentIndex(
            dialog.preset_combo.findData(BackupPreset.CUSTOM.value)
        )
        dialog.component_checks[BackupComponent.COUNTER_VALUES].setChecked(True)

        self.assertIn(
            BackupComponent.COUNTER_DEFINITIONS,
            dialog.selected_components(),
        )
        self.assertIn("Counter Values", dialog.summary_label.text())
        dialog.close()

    def test_restore_dialog_shows_schema_and_locks_dependencies(self) -> None:
        inspection = BackupInspection(
            Path("example.streamhousebackup"),
            "2026-09-11T16:04:00+00:00",
            "0.1.0",
            "manual",
            "custom",
            (
                BackupComponent.COUNTER_DEFINITIONS,
                BackupComponent.COUNTER_VALUES,
            ),
            {"counter_definitions": 2, "counter_values": 2},
        )
        dialog = RestoreSelectionDialog(inspection)

        definitions = dialog.component_checks[BackupComponent.COUNTER_DEFINITIONS]
        self.assertTrue(definitions.isChecked())
        self.assertFalse(definitions.isEnabled())
        self.assertIn("schema v2", definitions.text())
        self.assertEqual(
            set(dialog.selected_components()),
            {
                BackupComponent.COUNTER_DEFINITIONS,
                BackupComponent.COUNTER_VALUES,
            },
        )
        dialog.close()


if __name__ == "__main__":
    unittest.main()
