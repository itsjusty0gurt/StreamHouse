from __future__ import annotations

from html import escape

from PySide6.QtCore import QSignalBlocker, Qt, Slot
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.core.wiki_reference import (
    WIKI_CATEGORIES,
    WikiEntry,
    build_wiki_entries,
)
from products.hub.ui.automation_page import TaskEditorDialog
from products.hub.ui.page_header import PageHeader


class WikiPage(QWidget):
    """Read-only, local reference browser backed by Hub's live definitions."""

    def __init__(
        self,
        task_registry: TaskRegistry,
        variable_registry: VariableRegistry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("wikiPage")
        self.entries = build_wiki_entries(
            task_registry,
            variable_registry,
            task_schemas=TaskEditorDialog.SCHEMAS,
        )
        self._entries_by_id = {entry.entry_id: entry for entry in self.entries}
        self._compact = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)
        self.page_header = PageHeader(
            "Wiki",
            "Searchable reference for Streamhouse Hub tasks, triggers, Variables, services, and automation concepts.",
            self,
        )
        layout.addWidget(self.page_header)

        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("wikiSearch")
        self.search_edit.setPlaceholderText(
            "Search tasks, triggers, Variables, counters, Twitch, OBS…"
        )
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setObjectName("wikiSplitter")
        navigation = QWidget(self.splitter)
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 6, 0)
        navigation_layout.setSpacing(6)
        navigation_layout.addWidget(QLabel("Categories", navigation))
        self.category_list = QListWidget(navigation)
        self.category_list.setObjectName("wikiCategories")
        for category in WIKI_CATEGORIES:
            self.category_list.addItem(category)
        navigation_layout.addWidget(self.category_list, 0)
        self.result_label = QLabel("Entries", navigation)
        navigation_layout.addWidget(self.result_label)
        self.entry_list = QListWidget(navigation)
        self.entry_list.setObjectName("wikiEntries")
        self.entry_list.setWordWrap(True)
        navigation_layout.addWidget(self.entry_list, 1)

        reference = QWidget(self.splitter)
        reference_layout = QVBoxLayout(reference)
        reference_layout.setContentsMargins(8, 0, 0, 0)
        reference_layout.setSpacing(6)
        title_row = QHBoxLayout()
        self.title_label = QLabel("Select a reference entry", reference)
        self.title_label.setObjectName("wikiEntryTitle")
        title_font = self.title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        self.copy_button = QPushButton("Copy Variable", reference)
        self.copy_button.setObjectName("wikiCopyVariable")
        self.copy_button.hide()
        title_row.addWidget(self.copy_button)
        reference_layout.addLayout(title_row)
        self.summary_label = QLabel(
            "Choose a category and entry, or search across the complete local reference.",
            reference,
        )
        self.summary_label.setObjectName("wikiEntrySummary")
        self.summary_label.setWordWrap(True)
        reference_layout.addWidget(self.summary_label)
        self.browser = QTextBrowser(reference)
        self.browser.setObjectName("wikiEntryBody")
        self.browser.setOpenExternalLinks(False)
        reference_layout.addWidget(self.browser, 1)

        self.splitter.addWidget(navigation)
        self.splitter.addWidget(reference)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes((300, 760))
        layout.addWidget(self.splitter, 1)

        self.search_edit.textChanged.connect(self._refresh_entries)
        self.category_list.currentTextChanged.connect(self._category_changed)
        self.entry_list.currentItemChanged.connect(self._entry_changed)
        self.copy_button.clicked.connect(self._copy_current_variable)
        self.category_list.setCurrentRow(0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        threshold = 760
        hysteresis = 32
        compact = (
            self.width() < threshold + hysteresis
            if self._compact
            else self.width() < threshold - hysteresis
        )
        if compact == self._compact:
            return
        self._compact = compact
        self.splitter.setOrientation(
            Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal
        )
        self.category_list.setMaximumHeight(145 if compact else 16_777_215)
        self.entry_list.setMinimumHeight(130 if compact else 0)
        self.splitter.setSizes((260, 420) if compact else (300, 760))

    def matching_entries(self, query: str) -> tuple[WikiEntry, ...]:
        clean = query.strip().casefold()
        if not clean:
            return self.entries
        terms = tuple(part for part in clean.split() if part)
        return tuple(
            entry
            for entry in self.entries
            if all(term in entry.search_text() for term in terms)
        )

    def select_entry(self, entry_id: str) -> bool:
        row = self._entry_row(entry_id)
        if row >= 0:
            self.entry_list.setCurrentRow(row)
            return True
        entry = self._entries_by_id.get(entry_id)
        if entry is None:
            return False
        self.search_edit.clear()
        category_row = self._category_row(entry.category)
        if category_row >= 0:
            self.category_list.setCurrentRow(category_row)
        row = self._entry_row(entry_id)
        if row < 0:
            return False
        self.entry_list.setCurrentRow(row)
        return True

    def _entry_id_at(self, row: int) -> str:
        item = self.entry_list.item(row)
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _entry_row(self, entry_id: str) -> int:
        for row in range(self.entry_list.count()):
            if self._entry_id_at(row) == entry_id:
                return row
        return -1

    def _category_row(self, category: str) -> int:
        for row in range(self.category_list.count()):
            item = self.category_list.item(row)
            if item is not None and item.text() == category:
                return row
        return -1

    @Slot(str)
    def _category_changed(self, _category: str) -> None:
        if not self.search_edit.text().strip():
            self._refresh_entries()

    @Slot()
    def _refresh_entries(self) -> None:
        selected_id = self._entry_id_at(self.entry_list.currentRow())
        query = self.search_edit.text().strip()
        category = self.category_list.currentItem()
        category_name = category.text() if category is not None else WIKI_CATEGORIES[0]
        entries = self.matching_entries(query)
        if not query:
            entries = tuple(entry for entry in entries if entry.category == category_name)

        blocker = QSignalBlocker(self.entry_list)
        for target_row, entry in enumerate(entries):
            current_row = self._entry_row(entry.entry_id)
            if current_row < 0:
                item = QListWidgetItem()
                self.entry_list.insertItem(target_row, item)
            elif current_row != target_row:
                item = self.entry_list.takeItem(current_row)
                self.entry_list.insertItem(target_row, item)
            else:
                item = self.entry_list.item(target_row)
            item.setText(entry.title)
            item.setData(Qt.ItemDataRole.UserRole, entry.entry_id)
            item.setToolTip(entry.summary)

        while self.entry_list.count() > len(entries):
            removed = self.entry_list.takeItem(self.entry_list.count() - 1)
            del removed

        selected_row = self._entry_row(selected_id)
        if selected_row < 0 and entries:
            selected_row = 0
        self.entry_list.setCurrentRow(selected_row)
        del blocker

        self.result_label.setText(
            f"Search results ({len(entries)})" if query else f"{category_name} ({len(entries)})"
        )
        if selected_row >= 0:
            self._show_entry(entries[selected_row])
        else:
            self._show_entry(None)

    @Slot(object, object)
    def _entry_changed(self, current: object, _previous: object) -> None:
        if not isinstance(current, QListWidgetItem):
            self._show_entry(None)
            return
        self._show_entry(
            self._entries_by_id.get(
                str(current.data(Qt.ItemDataRole.UserRole) or "")
            )
        )

    def _show_entry(self, entry: WikiEntry | None) -> None:
        if entry is None:
            self.title_label.setText("No matching reference entries")
            self.summary_label.setText(
                "Try a broader search or choose another Wiki category."
            )
            self.browser.clear()
            self.copy_button.hide()
            self.copy_button.setProperty("copyText", "")
            return
        self.title_label.setText(entry.title)
        self.summary_label.setText(entry.summary)
        sections = []
        for section in entry.sections:
            lines = "".join(f"<li>{escape(line)}</li>" for line in section.lines)
            sections.append(f"<h3>{escape(section.title)}</h3><ul>{lines}</ul>")
        self.browser.setHtml("".join(sections))
        self.copy_button.setProperty("copyText", entry.copy_text)
        self.copy_button.setVisible(bool(entry.copy_text))

    @Slot()
    def _copy_current_variable(self) -> None:
        value = str(self.copy_button.property("copyText") or "")
        if value:
            QApplication.clipboard().setText(value)
