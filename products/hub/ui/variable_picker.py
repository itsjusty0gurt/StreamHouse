from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.variable_registry import VariableRegistry


class VariablePickerDialog(QDialog):
    """Reusable search-and-select dialog for canonical Hub variables."""

    def __init__(
        self,
        registry: VariableRegistry,
        context: Mapping[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.context = dict(context or {})
        self.setWindowTitle("Choose Variable")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search variables...")
        self.source_combo = QComboBox()
        self.source_combo.addItem("All sources", "")
        for source in sorted({item.source for item in registry.definitions()}):
            self.source_combo.addItem(source, source)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.source_combo)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Name", "Value", "Source", "Type"))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.details_label = QLabel()
        self.details_label.setWordWrap(True)
        layout.addWidget(self.details_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.copy_button = QPushButton("Copy Placeholder")
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.copy_button.clicked.connect(self._copy)
        layout.addWidget(buttons)
        self.search_edit.textChanged.connect(self.refresh)
        self.source_combo.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())
        self.refresh()

    def refresh(self) -> None:
        selected = self.selected_name()
        needle = self.search_edit.text().strip().casefold()
        source = str(self.source_combo.currentData() or "")
        self.table.setRowCount(0)
        for snapshot in self.registry.snapshots(self.context):
            definition = snapshot.definition
            searchable = " ".join(
                (definition.name, definition.display_name, definition.description)
            ).casefold()
            if needle and needle not in searchable:
                continue
            if source and definition.source != source:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(definition.name)
            name.setData(Qt.ItemDataRole.UserRole, definition.name)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(snapshot.display_value))
            self.table.setItem(row, 2, QTableWidgetItem(definition.source))
            self.table.setItem(row, 3, QTableWidgetItem(definition.data_type.value.title()))
            if definition.name == selected:
                self.table.selectRow(row)
        if self.table.rowCount() and self.table.currentRow() < 0:
            self.table.selectRow(0)

    def selected_name(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def selected_placeholder(self) -> str:
        name = self.selected_name()
        return f"{{{name}}}" if name else ""

    def _selection_changed(self) -> None:
        snapshot = self.registry.resolve(self.selected_name(), self.context)
        if snapshot is None:
            self.details_label.clear()
            return
        definition = snapshot.definition
        availability = definition.availability.value.title()
        access = "Read/write" if definition.writable else "Read-only"
        self.details_label.setText(
            f"{definition.description}\n{availability} - {access}"
        )

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        placeholder = self.selected_placeholder()
        if placeholder:
            QApplication.clipboard().setText(placeholder)
