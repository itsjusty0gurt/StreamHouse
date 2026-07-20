from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from twitch.models import TwitchCustomReward


class ChannelPointRewardDialog(QDialog):
    """Create or edit all Twitch-supported custom reward settings."""

    def __init__(
        self,
        reward: TwitchCustomReward | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.reward = reward
        self.setWindowTitle(
            "Edit Channel Point Reward" if reward else "New Channel Point Reward"
        )
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(45)
        self.cost_spin = QSpinBox()
        self.cost_spin.setRange(1, 2_000_000_000)
        self.cost_spin.setValue(100)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setFixedHeight(70)
        self.prompt_edit.setPlaceholderText(
            "Optional instructions shown when viewers redeem this reward"
        )
        self.color_edit = QLineEdit("#9147FF")
        self.color_edit.setMaxLength(7)
        self.enabled_check = QCheckBox("Viewers can redeem this reward")
        self.enabled_check.setChecked(True)
        self.input_required_check = QCheckBox(
            "Require the viewer to enter a message"
        )
        self.skip_queue_check = QCheckBox(
            "Skip the redemption request queue"
        )
        form.addRow("Title", self.title_edit)
        form.addRow("Cost", self.cost_spin)
        form.addRow("Prompt", self.prompt_edit)
        form.addRow("Background color", self.color_edit)
        form.addRow("", self.enabled_check)
        form.addRow("", self.input_required_check)
        form.addRow("", self.skip_queue_check)
        layout.addLayout(form)

        limits = QGroupBox("Redemption Limits")
        limit_layout = QFormLayout(limits)
        self.per_stream_check, self.per_stream_spin = self._limit_control(
            "Limit per stream", 1, 2_000_000_000
        )
        self.per_user_check, self.per_user_spin = self._limit_control(
            "Limit per viewer per stream", 1, 2_000_000_000
        )
        self.cooldown_check, self.cooldown_spin = self._limit_control(
            "Global cooldown", 1, 604_800, " seconds"
        )
        limit_layout.addRow(self.per_stream_check, self.per_stream_spin)
        limit_layout.addRow(self.per_user_check, self.per_user_spin)
        limit_layout.addRow(self.cooldown_check, self.cooldown_spin)
        layout.addWidget(limits)

        note = QLabel(
            "Twitch allows up to 50 custom rewards, including disabled rewards."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if reward is not None:
            self._load_reward(reward)

    @staticmethod
    def _limit_control(
        label: str,
        minimum: int,
        maximum: int,
        suffix: str = "",
    ) -> tuple[QCheckBox, QSpinBox]:
        check = QCheckBox(label)
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(minimum)
        spin.setSuffix(suffix)
        spin.setEnabled(False)
        check.toggled.connect(spin.setEnabled)
        return check, spin

    def _load_reward(self, reward: TwitchCustomReward) -> None:
        self.title_edit.setText(reward.title)
        self.cost_spin.setValue(max(1, reward.cost))
        self.prompt_edit.setPlainText(reward.prompt)
        self.color_edit.setText(reward.background_color or "#9147FF")
        self.enabled_check.setChecked(reward.is_enabled)
        self.input_required_check.setChecked(reward.is_user_input_required)
        self.skip_queue_check.setChecked(reward.should_skip_request_queue)
        self.per_stream_check.setChecked(reward.max_per_stream_enabled)
        self.per_stream_spin.setValue(max(1, reward.max_per_stream))
        self.per_user_check.setChecked(
            reward.max_per_user_per_stream_enabled
        )
        self.per_user_spin.setValue(max(1, reward.max_per_user_per_stream))
        self.cooldown_check.setChecked(reward.global_cooldown_enabled)
        self.cooldown_spin.setValue(max(1, reward.global_cooldown_seconds))

    def _validate_and_accept(self) -> None:
        title = self.title_edit.text().strip()
        prompt = self.prompt_edit.toPlainText().strip()
        color = self.color_edit.text().strip().upper()
        if not title:
            QMessageBox.warning(self, "Reward title", "Enter a reward title.")
            return
        if len(prompt) > 200:
            QMessageBox.warning(
                self,
                "Reward prompt",
                "The prompt must be 200 characters or fewer.",
            )
            return
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            QMessageBox.warning(
                self,
                "Background color",
                "Use a six-digit hex color such as #9147FF.",
            )
            return
        self.accept()

    def values(self) -> dict[str, object]:
        return {
            "title": self.title_edit.text().strip(),
            "cost": self.cost_spin.value(),
            "prompt": self.prompt_edit.toPlainText().strip(),
            "background_color": self.color_edit.text().strip().upper(),
            "is_enabled": self.enabled_check.isChecked(),
            "is_user_input_required": self.input_required_check.isChecked(),
            "is_max_per_stream_enabled": self.per_stream_check.isChecked(),
            "max_per_stream": self.per_stream_spin.value(),
            "is_max_per_user_per_stream_enabled": (
                self.per_user_check.isChecked()
            ),
            "max_per_user_per_stream": self.per_user_spin.value(),
            "is_global_cooldown_enabled": self.cooldown_check.isChecked(),
            "global_cooldown_seconds": self.cooldown_spin.value(),
            "should_redemptions_skip_request_queue": (
                self.skip_queue_check.isChecked()
            ),
        }
