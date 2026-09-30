from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QUrl, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont
from PySide6.QtWidgets import (
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
        self._closed = False
        self._chat_session = None
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
        self.close_button = QPushButton("Close", header)
        title_row.addWidget(self.always_on_top_checkbox)
        title_row.addWidget(self.open_twitch_button)
        title_row.addWidget(self.close_button)
        header_layout.addLayout(title_row)

        self.category_label = ElidingLabel(candidate.category, header)
        self.category_label.setStyleSheet("color:palette(highlight);")
        self.stream_title_label = ElidingLabel(candidate.title, header)
        self.stream_title_label.setStyleSheet("color:#adadb8;")
        header_layout.addWidget(self.category_label)
        header_layout.addWidget(self.stream_title_label)
        root.addWidget(header)

        chat_header = QHBoxLayout()
        chat_label = QLabel("Target Chat", self)
        chat_font = QFont(chat_label.font())
        chat_font.setBold(True)
        chat_label.setFont(chat_font)
        self.chat_status_label = QLabel("Connecting to target chat…", self)
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
        root.addLayout(chat_header)

        self.chat_view = TwitchChatView(self, history_limit=500)
        self.chat_view.setObjectName("raidLandingChat")
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
        self.close_button.clicked.connect(self.close)

        factory = chat_session_factory or service.create_temporary_chat_session
        try:
            self._chat_session = factory(candidate.user_id, self)
        except (PermissionError, ValueError) as error:
            self.chat_status_label.setText(str(error))
        else:
            self._chat_session.message_received.connect(self._receive_message)
            self._chat_session.state_changed.connect(self._set_chat_status)
            self._chat_session.start()

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
    def _set_chat_status(self, status: str) -> None:
        if not self._closed:
            self.chat_status_label.setText(status)

    @Slot()
    def _open_on_twitch(self) -> None:
        if self._closed:
            return
        self._url_opener(
            QUrl(f"https://www.twitch.tv/{self.candidate.login}")
        )

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
                session.state_changed.disconnect(self._set_chat_status)
            except (RuntimeError, TypeError):
                pass
            session.close()
        self.chat_view.clear()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.shutdown()
        event.accept()
        self.dismissed.emit(self)
