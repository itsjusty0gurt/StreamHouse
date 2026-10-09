from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from products.hub.counters.models import parse_counter_number
from products.hub.ui.page_header import PageHeader


class _JobSignals(QObject):
    done = Signal(str, object, object)


class _Job(QRunnable):
    def __init__(self, action, signals: _JobSignals):
        super().__init__()
        # The page releases the worker after its queued completion is handled.
        # Qt must not delete the QRunnable before that signal reaches the UI.
        self.setAutoDelete(False)
        self.token = uuid4().hex
        self.action = action
        self.signals = signals

    def run(self):
        try:
            result = self.action()
        except Exception as error:
            self.signals.done.emit(self.token, None, str(error))
        else:
            self.signals.done.emit(self.token, result, None)


@dataclass(frozen=True, slots=True)
class _CounterWriteRequest:
    counter_id: str
    scope: str
    value: object
    user_id: str
    user_login: str
    user_name: str
    stream_id: str


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
        user_groups=None,
        group_reference_count: Callable[[str], int] | None = None,
        first_message_store=None,
        first_words_changed: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.store, self.counters, self.stream_id = store, counters, stream_id
        self.open_user = open_user
        self.context_menu = context_menu
        self.user_groups = user_groups
        self.group_reference_count = group_reference_count or (lambda _group_id: 0)
        self.first_message_store = first_message_store
        self.first_words_changed = first_words_changed or (lambda: None)
        self.selected_id = ""
        self._signature = None
        self._counter_pending = False
        self._counter_write_pending = False
        self._counter_write_token = None
        self._counter_rows = []
        self._closing = False
        self._jobs: dict[str, tuple[_Job, Callable[[object, object], None]]] = {}
        # A retained runnable may finish after QWidget teardown has deleted the
        # page's C++ children. Keep the signal source independent so emitting
        # cannot call through a dead Shiboken wrapper.
        self._job_signals = _JobSignals()
        self._job_signals.done.connect(
            self._job_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        layout = QVBoxLayout(self)
        self.page_header = PageHeader("Users", parent=self)
        layout.addWidget(self.page_header)
        self.first_words_group = QGroupBox("First Words of Stream")
        first_words_layout = QVBoxLayout(self.first_words_group)
        self.first_words_enabled = QCheckBox("Enable First Message triggers")
        self.first_words_enabled.setTristate(True)
        self.first_words_reset = QPushButton("Reset First Words for Current Stream")
        first_words_layout.addWidget(self.first_words_enabled)
        first_words_layout.addWidget(
            self.first_words_reset, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self.first_words_status = QLabel()
        self.first_words_status.setWordWrap(True)
        first_words_layout.addWidget(self.first_words_status)
        self.first_words_feedback = QLabel()
        self.first_words_feedback.setWordWrap(True)
        first_words_layout.addWidget(self.first_words_feedback)
        layout.addWidget(self.first_words_group)
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
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
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
        user_groups = QGroupBox("User Groups")
        group_layout = QVBoxLayout(user_groups)
        group_help = QLabel(
            "Users may belong to several groups. System groups are protected; custom groups can be renamed or deleted."
        )
        group_help.setWordWrap(True)
        group_layout.addWidget(group_help)
        self.user_group_list = QListWidget()
        self.user_group_list.setObjectName("userGroupList")
        self.user_group_list.setMaximumHeight(150)
        group_layout.addWidget(self.user_group_list)
        group_actions = QHBoxLayout()
        self.create_group_button = QPushButton("Create")
        self.rename_group_button = QPushButton("Rename")
        self.delete_group_button = QPushButton("Delete")
        for button in (
            self.create_group_button,
            self.rename_group_button,
            self.delete_group_button,
        ):
            group_actions.addWidget(button)
        group_layout.addLayout(group_actions)
        self.group_members = QLabel("Select a user group to view its members.")
        self.group_members.setWordWrap(True)
        group_layout.addWidget(self.group_members)
        detail_layout.addWidget(user_groups)
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
        self.user_group_list.itemChanged.connect(self._user_group_membership_changed)
        self.user_group_list.itemSelectionChanged.connect(self._show_group_members)
        self.create_group_button.clicked.connect(self._create_group)
        self.rename_group_button.clicked.connect(self._rename_group)
        self.delete_group_button.clicked.connect(self._delete_group)
        self.edit_counter.clicked.connect(self._edit_counter)
        self.first_words_enabled.clicked.connect(self._set_first_words_enabled)
        self.first_words_reset.clicked.connect(self._reset_first_words)
        self.counter_table.itemSelectionChanged.connect(self._counter_selection)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._update_columns()
        self._refresh_first_words_controls()
        self.refresh()

    def _tick(self):
        if self.isVisible():
            self.refresh()
            if self.selected_id:
                self._load_counters()

    def _refresh_first_words_controls(self) -> None:
        store = self.first_message_store
        available = store is not None
        self.first_words_group.setVisible(available)
        if not available:
            return
        enabled, total = store.first_message_trigger_counts()
        active, viewer_count = store.first_message_stream_status()
        self.first_words_enabled.blockSignals(True)
        if total and enabled == total:
            state = Qt.CheckState.Checked
        elif enabled:
            state = Qt.CheckState.PartiallyChecked
        else:
            state = Qt.CheckState.Unchecked
        self.first_words_enabled.setCheckState(state)
        self.first_words_enabled.blockSignals(False)
        self.first_words_enabled.setEnabled(bool(total))
        self.first_words_reset.setEnabled(bool(total) and active)
        if not total:
            text = "Tracking unavailable — no First Message triggers are configured."
        elif not active:
            text = "No active Twitch stream."
        elif not enabled:
            text = "First Message triggers are disabled for this stream."
        else:
            noun = "viewer" if viewer_count == 1 else "viewers"
            text = f"Tracking this stream · {viewer_count} {noun} tracked."
            if enabled != total:
                text += f" {enabled} of {total} triggers enabled."
        self.first_words_status.setText(text)

    def _set_first_words_enabled(self, enabled: bool) -> None:
        store = self.first_message_store
        if store is None:
            return
        try:
            changed = store.set_first_message_triggers_enabled(enabled)
        except OSError as error:
            self.first_words_feedback.setText(f"Could not update First Words: {error}")
        else:
            self.first_words_feedback.setText(
                "First Message triggers enabled."
                if changed and enabled
                else "First Message triggers disabled."
                if changed
                else "No First Message triggers are configured."
            )
            if changed:
                self.first_words_changed()
        self._refresh_first_words_controls()

    def _reset_first_words(self) -> None:
        store = self.first_message_store
        if store is None:
            return
        if store.reset_current_stream_first_messages():
            self.first_words_feedback.setText(
                "First Words state reset for the current stream."
            )
        else:
            self.first_words_feedback.setText("No active Twitch stream to reset.")
        self._refresh_first_words_controls()

    def refresh(self, *, force=False):
        self._refresh_first_words_controls()
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
                record.is_bot,
                tuple(record.roles),
                tuple(record.twitch_status.items()),
            )
            for record in records
        )
        if self.user_groups is not None:
            signature += (
                tuple((group.group_id, group.name) for group in self.user_groups.list_groups()),
                tuple(
                    (user_id, tuple(sorted(group_ids)))
                    for user_id, group_ids in sorted(
                        self.user_groups.store.memberships.items()
                    )
                ),
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
            group_names = (
                [group.name for group in self.user_groups.groups_for_user(record.user_id)]
                if self.user_groups is not None
                else []
            )
            values = (
                record.user_name or record.user_id,
                f"@{record.user_login}" if record.user_login else "Unknown",
                ", ".join(group_names) or "Viewer",
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
        self._refresh_user_groups()
        if record is None:
            self.info.setText("Select a user to manage their profile and counters.")
            self.counter_table.setRowCount(0)
            self.edit_counter.setEnabled(False)
            return
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
                    f"Bot: {'Yes' if self.user_groups is not None and self.user_groups.is_bot(record.user_id) else 'No'}",
                    *roles,
                    f"First Seen: {local_timestamp(record.first_seen)}",
                    f"Last Seen: {local_timestamp(record.last_seen)}",
                    "Twitch roles reflect the latest observation, not a live lookup.",
                ]
            )
        )
        self._load_counters()

    def _refresh_user_groups(self) -> None:
        selected_group_id = self._selected_user_group_id()
        selected_user_ids = self._selected_user_ids()
        self.user_group_list.blockSignals(True)
        self.user_group_list.clear()
        for group in self.user_groups.list_groups() if self.user_groups is not None else ():
            item = QListWidgetItem(group.name)
            item.setData(Qt.ItemDataRole.UserRole, group.group_id)
            if group.membership_editable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            states = [
                self.user_groups.is_member(user_id, group.group_id)
                for user_id in selected_user_ids
            ]
            item.setCheckState(
                Qt.CheckState.Checked
                if states and all(states)
                else Qt.CheckState.PartiallyChecked
                if any(states)
                else Qt.CheckState.Unchecked
            )
            item.setToolTip(
                "Managed automatically by Hub."
                if not group.membership_editable
                else "Assign or remove this group for the selected user(s)."
            )
            self.user_group_list.addItem(item)
            if group.group_id == selected_group_id:
                self.user_group_list.setCurrentItem(item)
        self.user_group_list.blockSignals(False)
        enabled = self.user_groups is not None
        self.create_group_button.setEnabled(enabled)
        self._update_group_actions()
        self._show_group_members()

    def _selected_user_group_id(self) -> str:
        item = self.user_group_list.currentItem() if hasattr(self, "user_group_list") else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _user_group_membership_changed(self, item: QListWidgetItem) -> None:
        selected_user_ids = self._selected_user_ids()
        if self.user_groups is None or not selected_user_ids:
            self._refresh_user_groups()
            return
        group_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        try:
            for user_id in selected_user_ids:
                if item.checkState() == Qt.CheckState.Checked:
                    self.user_groups.assign_member(group_id, user_id)
                else:
                    self.user_groups.remove_member(group_id, user_id)
        except (OSError, ValueError) as error:
            self.status.setText(f"Could not update user group: {error}")
        else:
            self.status.setText("User group membership saved.")
        self.refresh(force=True)

    def _create_group(self) -> None:
        if self.user_groups is None:
            return
        name, accepted = QInputDialog.getText(self, "Create Custom Group", "Group name:")
        if not accepted:
            return
        try:
            group = self.user_groups.create_group(name)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Create Group", str(error))
            return
        self.refresh(force=True)
        self._select_user_group(group.group_id)

    def _rename_group(self) -> None:
        if self.user_groups is None:
            return
        group_id = self._selected_user_group_id()
        group = self.user_groups.get_group(group_id)
        if group is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename Custom Group", "Group name:", text=group.name
        )
        if not accepted:
            return
        try:
            self.user_groups.rename_group(group_id, name)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Rename Group", str(error))
            return
        self.refresh(force=True)
        self._select_user_group(group_id)

    def _delete_group(self) -> None:
        if self.user_groups is None:
            return
        group_id = self._selected_user_group_id()
        group = self.user_groups.get_group(group_id)
        if group is None:
            return
        members = len(self.user_groups.member_ids(group_id))
        references = self.group_reference_count(group_id)
        message = (
            f'Delete "{group.name}"?\n\n'
            f"Memberships removed: {members}\n"
            f"Automation references left unresolved: {references}\n\n"
            "Chatter records will not be changed."
        )
        if QMessageBox.question(self, "Delete Custom Group", message) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.user_groups.delete_group(group_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Delete Group", str(error))
            return
        self.refresh(force=True)

    def _select_user_group(self, group_id: str) -> None:
        for row in range(self.user_group_list.count()):
            item = self.user_group_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == group_id:
                self.user_group_list.setCurrentItem(item)
                break

    def _show_group_members(self) -> None:
        group_id = self._selected_user_group_id()
        self._update_group_actions()
        if self.user_groups is None or not group_id:
            self.group_members.setText("Select a user group to view its members.")
            return
        labels = []
        for user_id in self.user_groups.member_ids(group_id):
            record = self.store.records.get(user_id)
            labels.append(record.user_name if record is not None and record.user_name else user_id)
        self.group_members.setText(
            "Members: " + (", ".join(labels) if labels else "None")
        )

    def _selected_user_ids(self) -> tuple[str, ...]:
        ids = {
            str(item.data(Qt.ItemDataRole.UserRole) or "")
            for item in self.table.selectedItems()
            if item.column() == 0
        }
        if not ids and self.selected_id:
            ids.add(self.selected_id)
        return tuple(sorted(value for value in ids if value))

    def _update_group_actions(self) -> None:
        group = (
            self.user_groups.get_group(self._selected_user_group_id())
            if self.user_groups is not None
            else None
        )
        editable = group is not None and not group.protected
        self.rename_group_button.setEnabled(editable)
        self.delete_group_button.setEnabled(editable)

    def _menu(self, position):
        item = self.table.itemAt(position)
        if item is not None:
            user_id = item.data(Qt.ItemDataRole.UserRole)
            record = self.store.records[user_id]
            self.context_menu(user_id, record.user_name, "")

    def _load_counters(self):
        if self._closing or self._counter_pending:
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
        self._start_job(read, self._counters_loaded)

    def _start_job(self, action, completion) -> str:
        """Retain a worker until its queued UI-thread completion is delivered."""
        job = _Job(action, self._job_signals)
        self._jobs[job.token] = (job, completion)
        self.pool.start(job)
        return job.token

    @Slot(str, object, object)
    def _job_finished(self, token, result, error) -> None:
        entry = self._jobs.pop(token, None)
        if entry is None:
            return
        _job, completion = entry
        if self._closing:
            return
        completion(result, error)

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
        if self._closing:
            return
        record = self.store.records.get(self.selected_id)
        if record is None or scope not in {"viewer_total", "viewer_stream_total"}:
            return
        if self._counter_write_pending:
            self.status.setText("A Counter save is already in progress.")
            return
        definition = next(
            (
                row[0]
                for row in self._counter_rows
                if row[0].counter_id == str(counter_id).strip().casefold()
            ),
            None,
        )
        if definition is None:
            self.status.setText("The selected Counter is no longer available.")
            return
        if not definition.enabled:
            self.status.setText("The selected Counter is disabled.")
            return
        if not definition.tracks(scope):
            self.status.setText("The selected scope is not tracked by this Counter.")
            return
        stream_id = self.stream_id()
        if scope == "viewer_stream_total" and not stream_id:
            self.status.setText("A current Twitch stream is required for this value.")
            return
        try:
            exact_value = parse_counter_number(value, definition.numeric_type)
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))
            return
        if exact_value < definition.minimum:
            self.status.setText(f"Value cannot be below {definition.minimum}.")
            return
        request = _CounterWriteRequest(
            counter_id=definition.counter_id,
            scope=scope,
            value=exact_value,
            user_id=record.user_id,
            user_login=record.user_login,
            user_name=record.user_name,
            stream_id=stream_id,
        )
        # Give this explicit operation a full refresh interval so the periodic
        # reader cannot race the write-completion UI update.
        self.timer.start()
        self._counter_write_pending = True
        self.edit_counter.setEnabled(False)

        def save():
            operation = self.counters.set_value(
                request.counter_id,
                request.scope,
                request.value,
                user_id=request.user_id,
                login=request.user_login,
                display_name=request.user_name,
                stream_id=request.stream_id,
            )
            formatted = None
            if operation.status == "success":
                formatted = (
                    self.counters.format_value(
                        request.counter_id, operation.values.viewer_total
                    ),
                    self.counters.format_value(
                        request.counter_id, operation.values.viewer_stream_total
                    ),
                )
            return operation, formatted

        self._counter_write_token = self._start_job(
            save,
            lambda result, error: self._counter_saved(request, result, error),
        )

    def _counter_saved(self, request, result, error):
        self._counter_write_pending = False
        self._counter_write_token = None
        same_selection = request.user_id == self.selected_id
        same_stream = request.stream_id == self.stream_id()
        operation = None if result is None else result[0]
        formatted = None if result is None else result[1]
        if same_selection:
            if error:
                message = str(error)
            elif operation.status == "success":
                message = "Counter saved."
            else:
                message = operation.detail
            self.status.setText(message)
        if (
            same_selection
            and same_stream
            and operation is not None
            and operation.status == "success"
            and formatted is not None
        ):
            for row, (definition, _values, _lifetime, _stream) in enumerate(
                self._counter_rows
            ):
                if definition.counter_id != request.counter_id:
                    continue
                lifetime_text, stream_text = formatted
                self._counter_rows[row] = (
                    definition,
                    operation.values,
                    lifetime_text,
                    stream_text,
                )
                self.counter_table.item(row, 1).setText(lifetime_text)
                self.counter_table.item(row, 2).setText(
                    stream_text
                    if definition.track_viewer_stream_total and request.stream_id
                    else "Unavailable"
                )
                break
        elif same_selection and not same_stream:
            QTimer.singleShot(0, self, self._load_counters)
        self._counter_selection()

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
        self._closing = False
        self.timer.start()
        self.refresh(force=True)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop UI delivery while retained Counter jobs finish safely."""
        self._closing = True
        self.timer.stop()
        self._counter_pending = False
        self._counter_write_pending = False
        self._counter_write_token = None

    def _update_columns(self) -> None:
        # Identity and first-seen remain in details when compact columns hide.
        self.table.setColumnHidden(1, self.width() < 1_000)
        self.table.setColumnHidden(4, self.width() < 1_300)
