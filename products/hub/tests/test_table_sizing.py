from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from products.hub.ui.table_sizing import configure_semantic_columns


def test_semantic_table_columns_stretch_text_and_compact_metadata() -> None:
    application = QApplication.instance() or QApplication([])
    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(("Name", "Status", "Type", "Count"))

    configure_semantic_columns(table, stretch=(0,), compact=(1, 2, 3))

    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    for column in (1, 2, 3):
        assert (
            header.sectionResizeMode(column)
            == QHeaderView.ResizeMode.ResizeToContents
        )
    assert header.minimumSectionSize() == 56
    assert not header.stretchLastSection()
    table.setRowCount(1)
    for column, value in enumerate(("A useful identifying name", "On", "Text", "2")):
        table.setItem(0, column, QTableWidgetItem(value))
    table.resize(800, 240)
    table.show()
    application.processEvents()
    assert header.sectionSize(0) > header.sectionSize(1)

    table.resize(200, 240)
    application.processEvents()
    assert table.horizontalScrollBar().maximum() > 0
    table.deleteLater()
    application.processEvents()


def test_semantic_table_columns_support_multiple_text_columns() -> None:
    application = QApplication.instance() or QApplication([])
    table = QTableWidget(0, 5)

    configure_semantic_columns(table, stretch=(1, 2), compact=(0, 3, 4))

    header = table.horizontalHeader()
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    table.deleteLater()
    application.processEvents()
