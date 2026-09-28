from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from products.hub.twitch.auth import TwitchAuthService
from products.hub.twitch.automation_triggers import TwitchEventAutomationTrigger
from products.hub.twitch.models import TwitchCustomReward
from products.hub.twitch.service import TwitchService


class _DiscoverySignals(QObject):
    finished = Signal(int, object, object)


class _DiscoveryWorker(QRunnable):
    def __init__(self, request_id: int, operation: Callable[[], object]) -> None:
        super().__init__()
        self.request_id = request_id
        self.operation = operation
        self.signals = _DiscoverySignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self.request_id, self.operation(), None)
        except Exception as error:
            self.signals.finished.emit(self.request_id, None, error)


class ChannelPointRedemptionTriggerDialog(QDialog):
    """Select a stable Twitch custom-reward identity for an Automation trigger."""

    def __init__(
        self,
        service: TwitchService | None,
        auth: TwitchAuthService | None,
        parent: QWidget | None = None,
        trigger: TwitchEventAutomationTrigger | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.auth = auth
        self.saved_reward_id = trigger.reward_id if trigger else ""
        self.saved_reward_title = trigger.reward_title if trigger else ""
        self._request_id = 0
        self._workers: set[_DiscoveryWorker] = set()
        self.thread_pool = QThreadPool.globalInstance()
        self.setWindowTitle("Edit Channel Point Redemption" if trigger else "Add Channel Point Redemption")
        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.reward_combo = QComboBox()
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(trigger.enabled if trigger else True)
        form.addRow("Reward", self.reward_combo)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.authorize_button = QPushButton("Reconnect Twitch")
        self.authorize_button.clicked.connect(self._authorize)
        self.authorize_button.setVisible(False)
        layout.addWidget(self.authorize_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._restore_saved_selection()
        self.refresh_rewards()

    def values(self) -> dict[str, object]:
        reward_id = str(self.reward_combo.currentData() or "")
        return {
            "reward_id": reward_id,
            "reward_title": (
                self.reward_combo.currentText().removesuffix(" (Missing)")
                if reward_id
                else ""
            ),
            "enabled": self.enabled_check.isChecked(),
        }

    def refresh_rewards(self) -> None:
        self._request_id += 1
        request_id = self._request_id
        if self.service is None or self.auth is None or self.auth.token is None:
            self.status_label.setText("Sign in with your channel account to load rewards. Your saved selection is preserved.")
            self.authorize_button.setVisible(self.auth is not None)
            return
        self.status_label.setText("Loading custom rewards…")
        self.authorize_button.setVisible(False)
        worker = _DiscoveryWorker(request_id, self.service.get_custom_rewards_for_discovery)
        self._workers.add(worker)
        worker.signals.finished.connect(self._rewards_loaded)
        self.thread_pool.start(worker)

    @Slot(int, object, object)
    def _rewards_loaded(self, request_id: int, result: object, error: object) -> None:
        self._workers = {worker for worker in self._workers if worker.request_id != request_id}
        if request_id != self._request_id:
            return
        if error is not None:
            self.status_label.setText(f"Could not load rewards: {error}. Your saved selection is preserved.")
            detail = str(error).casefold()
            self.authorize_button.setVisible(
                "permission" in detail
                or "scope" in detail
                or "grant" in detail
                or "redemptions" in detail
            )
            return
        rewards = [item for item in (result or []) if isinstance(item, TwitchCustomReward)]
        current_id = str(self.reward_combo.currentData() or self.saved_reward_id)
        current_title = self.saved_reward_title
        self.reward_combo.clear()
        self.reward_combo.addItem("Any Custom Reward", "")
        for reward in sorted(rewards, key=lambda item: item.title.casefold()):
            self.reward_combo.addItem(reward.title, reward.id)
        index = self.reward_combo.findData(current_id)
        if current_id and index < 0:
            self.reward_combo.addItem(f"{current_title or current_id} (Missing)", current_id)
            index = self.reward_combo.count() - 1
        self.reward_combo.setCurrentIndex(max(index, 0))
        self.status_label.setText(f"Loaded {len(rewards)} custom reward(s).")

    def _restore_saved_selection(self) -> None:
        self.reward_combo.clear()
        self.reward_combo.addItem("Any Custom Reward", "")
        if self.saved_reward_id:
            self.reward_combo.addItem(
                f"{self.saved_reward_title or self.saved_reward_id} (Missing)",
                self.saved_reward_id,
            )
            self.reward_combo.setCurrentIndex(1)

    def _authorize(self) -> None:
        if self.auth is not None:
            self.auth.sign_in()
            self.status_label.setText("Complete Twitch authorization, then reopen this trigger to refresh rewards.")

    def reject(self) -> None:
        self._request_id += 1
        super().reject()
