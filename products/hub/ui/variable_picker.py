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

from products.hub.automation.variable_registry import (
    VariableDefinition,
    VariableRegistry,
    VariableSnapshot,
)


class VariablePickerDialog(QDialog):
    """Reusable search-and-select dialog for canonical Hub variables."""

    def __init__(
        self,
        registry: VariableRegistry,
        context: Mapping[str, object] | None = None,
        parent: QWidget | None = None,
        *,
        extra_definitions: tuple[VariableDefinition, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.context = dict(context or {})
        self.extra_definitions = extra_definitions
        self.setWindowTitle("Choose Variable")
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search variables...")
        self.source_combo = QComboBox()
        self.source_combo.addItem("All sources", "")
        definitions = (*registry.definitions(), *extra_definitions)
        for source in sorted({item.source for item in definitions}):
            self.source_combo.addItem(source, source)
        self.category_combo = QComboBox()
        self.category_combo.addItem("All categories", "")
        for category in sorted({item.category for item in definitions}):
            self.category_combo.addItem(category, category)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.source_combo)
        filters.addWidget(self.category_combo)
        layout.addLayout(filters)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Name", "Value / Preview", "Source", "Type", "Availability", "Access")
        )
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
        self.category_combo.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())
        self.refresh()

    def refresh(self) -> None:
        selected = self.selected_name()
        needle = self.search_edit.text().strip().casefold()
        source = str(self.source_combo.currentData() or "")
        category = str(self.category_combo.currentData() or "")
        self.table.setRowCount(0)
        for snapshot in self._snapshots():
            definition = snapshot.definition
            searchable = " ".join(
                (definition.name, definition.display_name, definition.description)
            ).casefold()
            if needle and needle not in searchable:
                continue
            if source and definition.source != source:
                continue
            if category and definition.category != category:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(definition.name)
            name.setData(Qt.ItemDataRole.UserRole, definition.name)
            self.table.setItem(row, 0, name)
            value = snapshot.display_value
            if not snapshot.available and definition.preview_value is not None:
                value = f"{definition.preview_value} (preview)"
            self.table.setItem(row, 1, QTableWidgetItem(value))
            self.table.setItem(row, 2, QTableWidgetItem(definition.source))
            self.table.setItem(row, 3, QTableWidgetItem(definition.data_type.value.title()))
            availability = (
                definition.availability.value.title()
                if snapshot.available
                else f"Unavailable — {definition.availability.value.title()}"
            )
            self.table.setItem(row, 4, QTableWidgetItem(availability))
            self.table.setItem(
                row,
                5,
                QTableWidgetItem(
                    "Read/write" if definition.writable else "Read-only"
                ),
            )
            if definition.name == selected:
                self.table.selectRow(row)
        if self.table.rowCount() and self.table.currentRow() < 0:
            self.table.selectRow(0)

    def _snapshots(self) -> tuple[VariableSnapshot, ...]:
        snapshots = list(self.registry.snapshots(self.context))
        known = {item.definition.name for item in snapshots}
        for definition in self.extra_definitions:
            if definition.name in known:
                continue
            available = definition.name in self.context
            snapshots.append(
                VariableSnapshot(
                    definition,
                    self.context.get(definition.name),
                    available,
                    "Available only during the routine after its producing task runs.",
                )
            )
        return tuple(snapshots)

    def selected_name(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def selected_placeholder(self) -> str:
        name = self.selected_name()
        return f"{{{name}}}" if name else ""

    def _selection_changed(self) -> None:
        snapshot = next(
            (
                item
                for item in self._snapshots()
                if item.definition.name == self.selected_name()
            ),
            None,
        )
        if snapshot is None:
            self.details_label.clear()
            return
        definition = snapshot.definition
        availability = definition.availability.value.title()
        access = "Read/write" if definition.writable else "Read-only"
        requirement = (
            f" Requires {', '.join(definition.required_context)}."
            if definition.required_context and not snapshot.available
            else ""
        )
        self.details_label.setText(
            f"{definition.description}\n{availability} - {access}.{requirement}"
        )

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        placeholder = self.selected_placeholder()
        if placeholder:
            QApplication.clipboard().setText(placeholder)
