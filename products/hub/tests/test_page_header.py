from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from products.hub.ui.page_header import PageHeader


class PageHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_title_optional_subtitle_and_action_are_rendered(self) -> None:
        header = PageHeader("Automation")
        action = QPushButton("New Routine")
        header.add_action(action)

        self.assertEqual(header.title_label.text(), "Automation")
        self.assertTrue(header.subtitle_label.isHidden())
        self.assertIs(action.parentWidget(), header.action_widget)
        self.assertFalse(header.action_widget.isHidden())

        header.set_subtitle("Manage routines")
        self.assertEqual(header.subtitle_label.text(), "Manage routines")
        self.assertFalse(header.subtitle_label.isHidden())

    def test_narrow_header_stacks_actions_with_hysteresis(self) -> None:
        header = PageHeader("A deliberately long page title", "Description")
        header.add_action(QPushButton("Primary Action"))
        header.show()

        header.resize(500, 100)
        self.application.processEvents()
        self.assertTrue(header.compact)

        header.resize(700, 100)
        self.application.processEvents()
        self.assertFalse(header.compact)
        header.close()
