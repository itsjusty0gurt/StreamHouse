from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QUrl, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.temporary_chat import TemporaryChatState
from products.hub.ui.automation_task_cards import ElidingLabel
from products.hub.ui.structured_twitch_chat_view import TwitchChatView


class RaidLandingWindow(QWidget):
    """Temporary local companion window for one confirmed outgoing raid."""

    dismissed = Signal(object)

    def __init__(
        self,
        candidate,
        service,
        parent: QWidget | None = None,
        *,
        url_opener: Callable[[QUrl], object] = QDesktopServices.openUrl,
        chat_session_factory=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.candidate = candidate
        self.service = service
        self._url_opener = url_opener
        self._chat_session_factory = (
            chat_session_factory or service.create_temporary_chat_session
        )
        self._closed = False
        self._chat_session = None
        self._chat_attempt_in_progress = False
        self.setObjectName("raidLandingWindow")
        self.setWindowTitle(f"Raid Landing — {candidate.display_name}")
        self.resize(640, 760)
        self.setMinimumSize(460, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QFrame(self)
        header.setObjectName("raidLandingHeader")
        header.setStyleSheet(
            "QFrame#raidLandingHeader {"
            "background:palette(base); border:1px solid palette(mid);"
            "border-radius:6px;"
            "}"
            "QFrame#raidLandingHeader QLabel {"
            "border:none; background:transparent;"
            "}"
        )
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(1)
        self.channel_name_label = ElidingLabel(candidate.display_name, header)
        name_font = QFont(self.channel_name_label.font())
        name_font.setBold(True)
        name_font.setPointSize(max(name_font.pointSize(), 14))
        self.channel_name_label.setFont(name_font)
        self.channel_login_label = QLabel(f"@{candidate.login}", header)
        self.channel_login_label.setStyleSheet("color:#adadb8;")
        title_stack.addWidget(self.channel_name_label)
        title_stack.addWidget(self.channel_login_label)
        title_row.addLayout(title_stack, 1)

        self.always_on_top_checkbox = QCheckBox("Always on Top", header)
        self.open_twitch_button = QPushButton("Open on Twitch", header)
        self.copy_link_button = QPushButton("Copy Channel Link", header)
        self.close_button = QPushButton("Close", header)
        title_row.addWidget(self.always_on_top_checkbox)
        header_layout.addLayout(title_row)

        metadata_available = bool(
            candidate.category.strip() or candidate.title.strip()
        )
        self.channel_state_label = QLabel(
            (
                "Live on Twitch"
                if metadata_available
                else "Channel information unavailable."
            ),
            header,
        )
        self.channel_state_label.setStyleSheet(
            "color:#bf94ff; font-weight:600;"
            if metadata_available
            else "color:#adadb8;"
        )
        self.category_label = ElidingLabel(
            candidate.category or "No category",
            header,
        )
        self.category_label.setStyleSheet("color:palette(highlight);")
        self.stream_title_label = ElidingLabel(
            candidate.title or "Title unavailable",
            header,
        )
        self.stream_title_label.setStyleSheet("color:#adadb8;")
        header_layout.addWidget(self.channel_state_label)
        header_layout.addWidget(self.category_label)
        header_layout.addWidget(self.stream_title_label)

        action_row = QHBoxLayout()
        action_row.addWidget(self.open_twitch_button)
        action_row.addWidget(self.copy_link_button)
        action_row.addStretch(1)
        action_row.addWidget(self.close_button)
        header_layout.addLayout(action_row)
        root.addWidget(header)

        chat_header = QHBoxLayout()
        chat_label = QLabel("Target Chat", self)
        chat_font = QFont(chat_label.font())
        chat_font.setBold(True)
        chat_label.setFont(chat_font)
        self.chat_status_label = QLabel("Connecting…", self)
        self.chat_status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.chat_status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.chat_status_label.setStyleSheet("color:#adadb8;")
        chat_header.addWidget(chat_label)
        chat_header.addWidget(self.chat_status_label, 1)
        self.retry_chat_button = QPushButton("Retry Chat", self)
        self.retry_chat_button.hide()
        chat_header.addWidget(self.retry_chat_button)
        root.addLayout(chat_header)

        self.chat_view = TwitchChatView(self, history_limit=500)
        self.chat_view.setObjectName("raidLandingChat")
        self._set_empty_chat_message("Waiting for chat…")
        root.addWidget(self.chat_view, 1)

        self.video_note_label = QLabel(
            "Use Open on Twitch for video. Target chat stays separate from "
            "your main Hub chat.",
            self,
        )
        self.video_note_label.setWordWrap(True)
        self.video_note_label.setStyleSheet("color:#adadb8;")
        root.addWidget(self.video_note_label)

        self.always_on_top_checkbox.toggled.connect(self._set_always_on_top)
        self.open_twitch_button.clicked.connect(self._open_on_twitch)
        self.copy_link_button.clicked.connect(self._copy_channel_link)
        self.retry_chat_button.clicked.connect(self._retry_chat)
        self.close_button.clicked.connect(self.close)

        has_login = bool(candidate.login.strip())
        self.open_twitch_button.setEnabled(has_login)
        self.copy_link_button.setEnabled(has_login)
        if has_login:
            self.open_twitch_button.setToolTip("Open this channel on Twitch.")
            self.copy_link_button.setToolTip("Copy this channel's Twitch link.")
        else:
            unavailable = "The target channel login is unavailable."
            self.open_twitch_button.setToolTip(unavailable)
            self.copy_link_button.setToolTip(unavailable)

        self._start_chat_session()

    def _start_chat_session(self) -> None:
        if self._closed or self._chat_attempt_in_progress:
            return
        self._chat_attempt_in_progress = True
        self.retry_chat_button.setEnabled(False)
        self.retry_chat_button.hide()
        self._set_chat_state(TemporaryChatState.CONNECTING.value)
        try:
            session = self._chat_session_factory(self.candidate.user_id, self)
        except (PermissionError, ValueError):
            self._chat_attempt_in_progress = False
            self._set_chat_state(TemporaryChatState.UNAVAILABLE.value)
        else:
            self._chat_session = session
            session.message_received.connect(self._receive_message)
            session.state_changed.connect(self._set_chat_state)
            session.start()

    @Slot(object)
    def _receive_message(self, message: object) -> None:
        if self._closed or not isinstance(message, TwitchMessage):
            return
        self.chat_view.append_message(
            message,
            username_color=message.color or "#bf94ff",
            show_timestamp=True,
        )

    @Slot(str)
    def _set_chat_state(self, state: str) -> None:
        if self._closed:
            return
        labels = {
            TemporaryChatState.CONNECTING.value: "Connecting…",
            TemporaryChatState.CONNECTED.value: "Connected",
            TemporaryChatState.RECONNECTING.value: "Reconnecting…",
            TemporaryChatState.UNAVAILABLE.value: "Chat unavailable",
            TemporaryChatState.DISCONNECTED.value: "Disconnected",
        }
        self.chat_status_label.setText(labels.get(state, "Chat unavailable"))
        self._chat_attempt_in_progress = state in {
            TemporaryChatState.CONNECTING.value,
            TemporaryChatState.RECONNECTING.value,
        }
        retryable = state in {
            TemporaryChatState.UNAVAILABLE.value,
            TemporaryChatState.DISCONNECTED.value,
        }
        self.retry_chat_button.setVisible(retryable)
        self.retry_chat_button.setEnabled(retryable)
        if not self.chat_view.history.entries:
            if state == TemporaryChatState.CONNECTED.value:
                self._set_empty_chat_message(
                    "No messages yet.<br>Waiting for chat…"
                )
            elif state == TemporaryChatState.UNAVAILABLE.value:
                self._set_empty_chat_message("Chat is temporarily unavailable.")
            elif state == TemporaryChatState.DISCONNECTED.value:
                self._set_empty_chat_message("Chat disconnected.")
            else:
                self._set_empty_chat_message(
                    labels.get(state, "Waiting for chat…")
                )

    def _set_empty_chat_message(self, message: str) -> None:
        self.chat_view.setHtml(
            "<div style='color:#adadb8;padding:18px;text-align:center;'>"
            f"{message}</div>"
        )

    @Slot()
    def _retry_chat(self) -> None:
        if self._closed or self._chat_attempt_in_progress:
            return
        previous = self._chat_session
        self._chat_session = None
        if previous is not None:
            try:
                previous.message_received.disconnect(self._receive_message)
                previous.state_changed.disconnect(self._set_chat_state)
            except (RuntimeError, TypeError):
                pass
            previous.close()
            previous.deleteLater()
        self._start_chat_session()

    @Slot()
    def _open_on_twitch(self) -> None:
        url = self._channel_url()
        if self._closed or url is None:
            return
        self._url_opener(url)

    @Slot()
    def _copy_channel_link(self) -> None:
        url = self._channel_url()
        if self._closed or url is None:
            return
        QApplication.clipboard().setText(url.toString())

    def _channel_url(self) -> QUrl | None:
        login = self.candidate.login.strip()
        return QUrl(f"https://www.twitch.tv/{login}") if login else None

    @Slot(bool)
    def _set_always_on_top(self, enabled: bool) -> None:
        if self._closed:
            return
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        session = self._chat_session
        self._chat_session = None
        if session is not None:
            try:
                session.message_received.disconnect(self._receive_message)
                session.state_changed.disconnect(self._set_chat_state)
            except (RuntimeError, TypeError):
                pass
            session.close()
        self.chat_view.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.shutdown()
        event.accept()
        self.dismissed.emit(self)
