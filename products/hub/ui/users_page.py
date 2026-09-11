from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from products.hub.ui.page_header import PageHeader


class _JobSignals(QObject):
    done = Signal(object, object)


class _Job(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.signals = _JobSignals()

    def run(self):
        try:
            result = self.action()
        except Exception as error:
            self.signals.done.emit(None, str(error))
        else:
            self.signals.done.emit(result, None)


def local_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%b %d, %Y %I:%M %p")
    except (ValueError, AttributeError):
        return "Unknown"


class UsersPage(QWidget):
    """Management view over chatter identity and the existing CounterService."""

    def __init__(
        self,
        store,
        counters,
        stream_id: Callable[[], str],
        profile,
        open_user,
        context_menu,
        set_group,
        parent=None,
    ):
        super().__init__(parent)
        self.store, self.counters, self.stream_id = store, counters, stream_id
        self.open_user = open_user
        self.context_menu = context_menu
        self.set_group = set_group
        self.selected_id = ""
        self._signature = None
        self._counter_pending = False
        self._counter_write_pending = False
        self._counter_rows = []
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        layout = QVBoxLayout(self)
        self.page_header = PageHeader("Users", parent=self)
        layout.addWidget(self.page_header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search users by name or login…")
        layout.addWidget(self.search)
        self.empty = QLabel(
            "No users recorded yet. Users appear as Hub observes Twitch chat/activity."
        )
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter, 1)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("usersTable")
        self.table.setHorizontalHeaderLabels(
            ["Display Name", "Login", "Group", "Status", "First Seen", "Last Seen"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.splitter.addWidget(self.table)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.info = QLabel("Select a user to manage their profile and counters.")
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.info)
        self.group = QComboBox()
        for label, value in (
            ("Automatic", ""),
            ("Regulars", "Regulars"),
            ("Bots", "Bots"),
            ("Viewers", "Viewers"),
        ):
            self.group.addItem(label, value)
        form = QFormLayout()
        form.addRow("Group", self.group)
        detail_layout.addLayout(form)
        detail_layout.addWidget(QLabel("Viewer Counters"))
        self.counter_table = QTableWidget(0, 3)
        self.counter_table.setHorizontalHeaderLabels(
            ["Counter", "Lifetime", "Current Stream"]
        )
        self.counter_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.counter_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.counter_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.counter_table.verticalHeader().hide()
        self.counter_table.setMaximumHeight(180)
        detail_layout.addWidget(self.counter_table)
        self.edit_counter = QPushButton("Edit Selected Counter Value")
        detail_layout.addWidget(self.edit_counter)
        self.status = QLabel()
        self.status.setWordWrap(True)
        detail_layout.addWidget(self.status)
        detail_layout.addWidget(profile)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(detail)
        self.splitter.addWidget(scroll)
        self.splitter.setSizes([600, 400])
        self.search.textChanged.connect(lambda: self.refresh(force=True))
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.customContextMenuRequested.connect(self._menu)
        self.group.activated.connect(self._group_changed)
        self.edit_counter.clicked.connect(self._edit_counter)
        self.counter_table.itemSelectionChanged.connect(self._counter_selection)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._update_columns()
        self.refresh()

    def _tick(self):
        if self.isVisible():
            self.refresh()
            if self.selected_id:
                self._load_counters()

    def refresh(self, *, force=False):
        records = sorted(
            self.store.records.values(), key=lambda record: record.last_seen, reverse=True
        )
        signature = tuple(
            (
                record.user_id,
                record.user_name,
                record.user_login,
                record.last_seen,
                record.first_seen,
                record.manual_group,
                record.is_bot,
                tuple(record.roles),
                tuple(record.twitch_status.items()),
            )
            for record in records
        )
        if not force and signature == self._signature:
            return
        self._signature = signature
        query = self.search.text().strip().casefold()
        records = [
            record
            for record in records
            if query
            in f"{record.user_name} {record.user_login} {record.user_id}".casefold()
        ]
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        selected_row = -1
        for row, record in enumerate(records):
            status = [key for key, value in record.twitch_status.items() if value]
            if self.store.is_bot(record.user_id):
                status.append("Bot")
            values = (
                record.user_name or record.user_id,
                f"@{record.user_login}" if record.user_login else "Unknown",
                record.manual_group or "Automatic",
                " · ".join(status) or "—",
                local_timestamp(record.first_seen),
                local_timestamp(record.last_seen),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.user_id)
                self.table.setItem(row, col, item)
            if record.user_id == self.selected_id:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self.empty.setVisible(not records)
        self.empty.setText(
            "No matching users."
            if query
            else "No users recorded yet. Users appear as Hub observes Twitch chat/activity."
        )
        self._show_details()

    def select_user(self, user_id):
        self.selected_id = user_id
        if self.search.text():
            self.search.clear()
        self.refresh(force=True)

    def _selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.selected_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        record = self.store.records[self.selected_id]
        self.open_user(None, record.user_id, record.user_name)

    def _show_details(self):
        record = self.store.records.get(self.selected_id)
        self.group.setEnabled(record is not None)
        if record is None:
            self.info.setText("Select a user to manage their profile and counters.")
            self.counter_table.setRowCount(0)
            self.edit_counter.setEnabled(False)
            return
        self.group.setCurrentIndex(max(0, self.group.findData(record.manual_group)))
        roles = []
        for role in ("Moderator", "VIP", "Subscriber"):
            value = record.twitch_status.get(role, True if role in record.roles else None)
            rendered = "Unknown" if value is None else "Yes" if value else "No"
            roles.append(f"{role}: {rendered}")
        self.info.setText(
            "\n".join(
                [
                    record.user_name,
                    f"Login: {record.user_login or 'Unknown'}",
                    f"Twitch ID: {record.user_id}",
                    f"Bot: {'Yes' if self.store.is_bot(record.user_id) else 'No'}",
                    *roles,
                    f"First Seen: {local_timestamp(record.first_seen)}",
                    f"Last Seen: {local_timestamp(record.last_seen)}",
                    "Twitch roles reflect the latest observation, not a live lookup.",
                ]
            )
        )
        self._load_counters()

    def _group_changed(self):
        if self.selected_id:
            self.set_group(self.selected_id, self.group.currentData())
            self.refresh(force=True)

    def _menu(self, position):
        item = self.table.itemAt(position)
        if item is not None:
            user_id = item.data(Qt.ItemDataRole.UserRole)
            record = self.store.records[user_id]
            self.context_menu(user_id, record.user_name, "")

    def _load_counters(self):
        if self._counter_pending:
            return
        user_id, stream_id = self.selected_id, self.stream_id()
        self._counter_pending = True

        def read():
            rows = []
            for definition in self.counters.list_counters():
                if not (definition.track_viewer_total or definition.track_viewer_stream_total):
                    continue
                values = self.counters.get_values(
                    definition.counter_id, user_id=user_id, stream_id=stream_id
                )
                rows.append((
                    definition,
                    values,
                    self.counters.format_value(
                        definition.counter_id, values.viewer_total
                    ),
                    self.counters.format_value(
                        definition.counter_id, values.viewer_stream_total
                    ),
                ))
            return user_id, stream_id, rows
        job = _Job(read)
        job.signals.done.connect(self._counters_loaded)
        self.pool.start(job)

    @Slot(object, object)
    def _counters_loaded(self, result, error):
        self._counter_pending = False
        if error:
            self.status.setText(str(error))
            return
        user_id, stream_id, rows = result
        if user_id != self.selected_id or stream_id != self.stream_id():
            if self.selected_id:
                self._load_counters()
            return
        self._counter_rows = rows
        self.counter_table.setRowCount(len(rows))
        for row, (definition, values, lifetime_text, stream_text) in enumerate(rows):
            self.counter_table.setItem(row, 0, QTableWidgetItem(definition.display_name))
            for col, scope in ((1, "viewer_total"), (2, "viewer_stream_total")):
                available = (
                    definition.enabled
                    and definition.tracks(scope)
                    and (col == 1 or bool(stream_id))
                )
                formatted = lifetime_text if col == 1 else stream_text
                text = formatted if available else "Unavailable"
                self.counter_table.setItem(row, col, QTableWidgetItem(text))
        self._counter_selection()

    def _counter_selection(self):
        row = self.counter_table.currentRow()
        editable = False
        if 0 <= row < len(self._counter_rows):
            definition = self._counter_rows[row][0]
            editable = definition.enabled and (
                definition.track_viewer_total
                or (definition.track_viewer_stream_total and bool(self.stream_id()))
            )
        self.edit_counter.setEnabled(
            editable and bool(self.selected_id) and not self._counter_write_pending
        )

    def _edit_counter(self):
        row = self.counter_table.currentRow()
        if row < 0 or row >= len(self._counter_rows):
            return
        definition, values, _lifetime_text, _stream_text = self._counter_rows[row]
        record = self.store.records.get(self.selected_id)
        stream_id = self.stream_id()
        if record is None or not definition.enabled:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit Counter — {definition.display_name}")
        form = QFormLayout(dialog)
        form.addRow(QLabel(f"User: {record.user_name}"))
        scope_combo = QComboBox()
        for label, scope in (("Lifetime", "viewer_total"), ("Current Stream", "viewer_stream_total")):
            if definition.tracks(scope) and (scope == "viewer_total" or stream_id):
                scope_combo.addItem(label, scope)
        if not scope_combo.count():
            return
        value = QLineEdit(str(getattr(values, scope_combo.currentData())))
        scope_combo.currentIndexChanged.connect(
            lambda: value.setText(str(getattr(values, scope_combo.currentData())))
        )
        form.addRow("Scope", scope_combo)
        form.addRow("Exact value", value)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.set_counter_value(definition.counter_id, scope_combo.currentData(), value.text())

    def set_counter_value(self, counter_id: str, scope: str, value: str) -> None:
        """Persist one selected viewer scope through CounterService."""
        record = self.store.records.get(self.selected_id)
        if record is None or scope not in {"viewer_total", "viewer_stream_total"}:
            return
        stream_id = self.stream_id()
        if scope == "viewer_stream_total" and not stream_id:
            self.status.setText("A current Twitch stream is required for this value.")
            return
        self._counter_write_pending = True
        self.edit_counter.setEnabled(False)
        job = _Job(
            lambda: self.counters.set_value(
                counter_id,
                scope,
                value,
                user_id=record.user_id,
                login=record.user_login,
                display_name=record.user_name,
                stream_id=stream_id,
            )
        )
        job.signals.done.connect(self._counter_saved)
        self.pool.start(job)

    @Slot(object, object)
    def _counter_saved(self, result, error):
        self._counter_write_pending = False
        if error:
            message = str(error)
        elif result.status == "success":
            message = "Counter saved."
        else:
            message = result.detail
        self.status.setText(message)
        self._load_counters()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.splitter.setOrientation(
            Qt.Orientation.Vertical
            if self.width() < 850
            else Qt.Orientation.Horizontal
        )
        self._update_columns()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh(force=True)

    def _update_columns(self) -> None:
        # Identity and first-seen remain in details when compact columns hide.
        self.table.setColumnHidden(1, self.width() < 1_000)
        self.table.setColumnHidden(4, self.width() < 1_300)
