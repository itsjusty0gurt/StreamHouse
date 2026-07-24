from __future__ import annotations

import json
from collections.abc import Callable
from urllib.error import HTTPError

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from twitch.auth import TwitchAuthService
from twitch.models import TwitchCustomReward
from twitch.service import TwitchService
from ui.channel_point_reward_dialog import ChannelPointRewardDialog


class _RewardWorkerSignals(QObject):
    finished = Signal(object, object)


class _RewardWorker(QRunnable):
    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = _RewardWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self.operation(), None)
        except Exception as error:  # Network errors are returned to the UI.
            self.signals.finished.emit(None, error)


class ChannelPointsPage(QWidget):
    """Manage the signed-in broadcaster's Twitch custom rewards."""

    REQUIRED_SCOPE = "channel:manage:redemptions"

    def __init__(
        self,
        service: TwitchService,
        auth: TwitchAuthService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.auth = auth
        self.rewards: list[TwitchCustomReward] = []
        self.loaded = False
        self.busy = False
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(1)
        self._workers: set[_RewardWorker] = set()

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.create_button = QPushButton("Create Reward")
        self.edit_button = QPushButton("Edit Selected")
        self.toggle_button = QPushButton("Disable Selected")
        self.delete_button = QPushButton("Delete Selected")
        self.refresh_button = QPushButton("Refresh")
        toolbar.addWidget(self.create_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.toggle_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addStretch()
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 8)
        self.table.setObjectName("channelPointRewardsTable")
        self.table.setHorizontalHeaderLabels(
            (
                "State",
                "Reward",
                "Cost",
                "Viewer input",
                "Per stream",
                "Per viewer",
                "Cooldown",
                "Managed by",
            )
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((90, 230, 85, 110, 105, 105, 100, 100)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel(
            "Open this tab while signed in to load your custom rewards."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.create_button.clicked.connect(self._create_reward)
        self.edit_button.clicked.connect(self._edit_reward)
        self.toggle_button.clicked.connect(self._toggle_reward)
        self.delete_button.clicked.connect(self._delete_reward)
        self.refresh_button.clicked.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit_reward())
        self._update_actions()

    def _has_scope(self) -> bool:
        token = self.auth.token
        return token is not None and self.REQUIRED_SCOPE in set(token.scopes)

    def activate(self) -> None:
        if self.auth.token is None:
            self.status_label.setText(
                "Sign in with your channel account to manage Channel Points."
            )
            self._update_actions()
            return
        if not self._has_scope():
            self.status_label.setText(
                "Update Twitch permissions to allow Channel Point reward management."
            )
            self._update_actions()
            return
        if not self.loaded:
            self.refresh()

    def auth_changed(self) -> None:
        self.loaded = False
        if self.auth.token is None:
            self.rewards = []
            self._populate()
        self.activate()

    @Slot()
    def refresh(self) -> None:
        if self.busy:
            return
        if not self._has_scope():
            self.activate()
            return
        self._start_operation(
            "Loading Channel Point rewards...",
            self.service.get_custom_rewards,
            self._rewards_loaded,
        )

    def _rewards_loaded(self, result: object) -> None:
        self.rewards = list(result) if isinstance(result, list) else []
        self.loaded = True
        self._populate()
        count = len(self.rewards)
        self.status_label.setText(
            f"{count} custom reward{'s' if count != 1 else ''}. "
            "Rewards made outside Sally are shown read-only."
        )

    def _populate(self, selected_id: str = "") -> None:
        self.table.setRowCount(len(self.rewards))
        selected_row = -1
        for row, reward in enumerate(self.rewards):
            if reward.is_paused:
                state = "Paused"
            elif reward.is_enabled:
                state = "Enabled" if reward.is_in_stock else "Out of stock"
            else:
                state = "Disabled"
            values = (
                state,
                reward.title,
                f"{reward.cost:,}",
                "Required" if reward.is_user_input_required else "No",
                str(reward.max_per_stream)
                if reward.max_per_stream_enabled
                else "Unlimited",
                str(reward.max_per_user_per_stream)
                if reward.max_per_user_per_stream_enabled
                else "Unlimited",
                f"{reward.global_cooldown_seconds}s"
                if reward.global_cooldown_enabled
                else "None",
                "Sally" if reward.manageable else "Other app",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, reward)
                self.table.setItem(row, column, item)
            if reward.id == selected_id:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        self._update_actions()

    def selected_reward(self) -> TwitchCustomReward | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, TwitchCustomReward) else None

    @Slot()
    def _create_reward(self) -> None:
        dialog = ChannelPointRewardDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._start_operation(
            "Creating reward...",
            lambda: self.service.create_custom_reward(dialog.values()),
            lambda result: self._operation_saved(result, "Reward created."),
        )

    @Slot()
    def _edit_reward(self) -> None:
        reward = self.selected_reward()
        if reward is None or not reward.manageable:
            return
        dialog = ChannelPointRewardDialog(reward, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._start_operation(
            "Saving reward...",
            lambda: self.service.update_custom_reward(
                reward.id, dialog.values()
            ),
            lambda result: self._operation_saved(result, "Reward updated."),
        )

    @Slot()
    def _toggle_reward(self) -> None:
        reward = self.selected_reward()
        if reward is None or not reward.manageable:
            return
        self._start_operation(
            "Updating reward...",
            lambda: self.service.update_custom_reward(
                reward.id, {"is_enabled": not reward.is_enabled}
            ),
            lambda result: self._operation_saved(result, "Reward updated."),
        )

    @Slot()
    def _delete_reward(self) -> None:
        reward = self.selected_reward()
        if reward is None or not reward.manageable:
            return
        answer = QMessageBox.question(
            self,
            "Delete Channel Point Reward",
            f'Delete "{reward.title}" from Twitch? This cannot be undone.',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_operation(
            "Deleting reward...",
            lambda: self.service.delete_custom_reward(reward.id),
            lambda _result: self._operation_deleted(reward.id),
        )

    def _operation_saved(self, result: object, message: str) -> None:
        if not isinstance(result, TwitchCustomReward):
            self.status_label.setText("Twitch returned an invalid reward.")
            return
        self.rewards = [
            result if reward.id == result.id else reward
            for reward in self.rewards
        ]
        if all(reward.id != result.id for reward in self.rewards):
            self.rewards.append(result)
        self.rewards.sort(key=lambda reward: reward.title.casefold())
        self._populate(result.id)
        self.status_label.setText(message)

    def _operation_deleted(self, reward_id: str) -> None:
        self.rewards = [reward for reward in self.rewards if reward.id != reward_id]
        self._populate()
        self.status_label.setText("Reward deleted.")

    def _start_operation(
        self,
        message: str,
        operation: Callable[[], object],
        completed: Callable[[object], None],
    ) -> None:
        self.busy = True
        self.status_label.setText(message)
        self._update_actions()
        worker = _RewardWorker(operation)
        self._workers.add(worker)

        def finish(result: object, error: object) -> None:
            self._workers.discard(worker)
            self.busy = False
            if isinstance(error, BaseException):
                self.status_label.setText(self._error_message(error))
            else:
                completed(result)
            self._update_actions()

        worker.signals.finished.connect(finish)
        self.thread_pool.start(worker)

    @staticmethod
    def _error_message(error: BaseException) -> str:
        detail = str(error)
        if isinstance(error, HTTPError):
            try:
                payload = json.loads(error.read().decode("utf-8"))
                detail = str(payload.get("message", detail))
            except (OSError, ValueError, AttributeError):
                pass
            if error.code == 403:
                return (
                    "Twitch refused this action. Channel Points require an "
                    "Affiliate or Partner channel, and only Sally-created "
                    "rewards can be edited here."
                )
        return f"Channel Point request failed: {detail}"

    @Slot()
    def _update_actions(self) -> None:
        reward = self.selected_reward()
        authorized = self._has_scope() and not self.busy
        manageable = bool(reward and reward.manageable and authorized)
        self.create_button.setEnabled(authorized)
        self.refresh_button.setEnabled(authorized)
        self.edit_button.setEnabled(manageable)
        self.toggle_button.setEnabled(manageable)
        self.delete_button.setEnabled(manageable)
        self.toggle_button.setText(
            "Disable Selected"
            if reward is None or reward.is_enabled
            else "Enable Selected"
        )

    def shutdown(self) -> None:
        self.thread_pool.clear()
        self.thread_pool.waitForDone(2_000)
