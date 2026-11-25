"""Lazy read-only Variable Inspector inside the existing Run Details dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from products.hub.automation.history_variables import HistoryVariable, VariableHistoryView


class RunHistoryVariableInspector(QWidget):
    def __init__(
        self,
        initial: VariableHistoryView | None,
        final: VariableHistoryView | None,
        parent: QWidget | None = None,
        *,
        historical_values: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(parent)
        if final is None and historical_values:
            final = VariableHistoryView(tuple(
                HistoryVariable(name, "Unknown", "Populated" if value else "Empty", value)
                for name, value in historical_values
            ))
        self._views = {"Final": final, "Initial": initial}
        self._rows: list[HistoryVariable] = []
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.view_combo = QComboBox()
        self.view_combo.setAccessibleName("Execution boundary")
        self.view_combo.addItems(("Final", "Initial"))
        controls.addWidget(self.view_combo)
        self.search = QLineEdit()
        self.search.setAccessibleName("Filter captured Variables")
        self.search.setPlaceholderText("Filter by name, source, or state…")
        controls.addWidget(self.search, 1)
        layout.addLayout(controls)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("runHistoryContextTable")
        self.table.setHorizontalHeaderLabels(("Variable", "Value", "Source", "Comparison"))
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(2, 115)
        self.table.setColumnWidth(3, 110)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table, 1)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName("Selected captured value")
        self.detail.setPlaceholderText("Select a Variable to read its retained value.")
        self.detail.setMaximumHeight(108)
        layout.addWidget(self.detail)
        controls = QHBoxLayout()
        self.copy_name = QPushButton("Copy Variable Name")
        self.copy_value = QPushButton("Copy Value")
        controls.addWidget(self.copy_name)
        controls.addWidget(self.copy_value)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.search.textChanged.connect(self._refresh)
        self.view_combo.currentTextChanged.connect(self._refresh)
        self.table.itemSelectionChanged.connect(self._update_selection)
        self.copy_name.clicked.connect(self._copy_name)
        self.copy_value.clicked.connect(self._copy_value)
        self._refresh()

    def _refresh(self) -> None:
        selected = self._selection()
        boundary = self.view_combo.currentText()
        current = self._views[boundary]
        other = self._views["Initial" if boundary == "Final" else "Final"]
        if current is None:
            message = "No captured execution context was recorded for this view."
        elif current.limited:
            message = "Captured execution context is incomplete: some values or rows were omitted."
        else:
            message = "Captured execution context only; this is not a list of all available Variables."
        self.notice.setText(message)
        token = self.search.text().strip().casefold()
        self._rows = []
        self.table.setRowCount(0)
        if current is not None:
            values = {item.name: item for item in current.variables}
            comparison = {item.name: item for item in other.variables} if other else {}
            for name in sorted(values.keys() | comparison.keys()):
                item = values.get(name)
                opposite = comparison.get(name)
                change = ""
                if item is None:
                    item = HistoryVariable(name, opposite.source, "Not captured")
                if other is not None and name in values and opposite is not None:
                    if item.value is not None and opposite.value is not None:
                        change = "Changed" if item.value != opposite.value else ""
                    elif item.state != opposite.state:
                        change = "State changed"
                elif other is not None:
                    change = "One view only"
                if token and token not in f"{name} {item.source} {item.state}".casefold():
                    continue
                row = len(self._rows)
                self._rows.append(item)
                self.table.insertRow(row)
                preview = item.state
                if item.value:
                    single_line = " ".join(item.value.splitlines())
                    preview = single_line if len(single_line) <= 100 else single_line[:100] + "…"
                for column, text in enumerate((name, preview, item.source, change)):
                    self.table.setItem(row, column, QTableWidgetItem(text))
        if selected is not None:
            for row, item in enumerate(self._rows):
                if item.name == selected.name:
                    self.table.selectRow(row)
                    break
        self._update_selection()

    def _selection(self) -> HistoryVariable | None:
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _update_selection(self) -> None:
        item = self._selection()
        self.copy_name.setEnabled(item is not None)
        self.copy_value.setEnabled(item is not None and item.value is not None)
        self.detail.setPlainText("" if item is None else item.value or item.state)

    def _copy_name(self) -> None:
        item = self._selection()
        if item is not None:
            QApplication.clipboard().setText(item.name)

    def _copy_value(self) -> None:
        item = self._selection()
        if item is not None and item.value is not None:
            QApplication.clipboard().setText(item.value)
