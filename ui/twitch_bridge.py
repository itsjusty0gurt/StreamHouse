from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from twitch.models import (
    TwitchChatNotice,
    TwitchEvent,
    TwitchEventDiagnostic,
    TwitchMessage,
)
from twitch.auth import TwitchAuthState
from twitch.service import TwitchConnectionState


class TwitchEventBridge(QObject):
    """Forward Twitch application events safely onto Qt's UI thread."""

    status_changed = Signal(object, str)
    message_received = Signal(object)
    error_received = Signal(str)
    diagnostic_received = Signal(object)
    auth_changed = Signal(object, str)
    bot_auth_changed = Signal(object, str)
    notice_received = Signal(object)
    activity_received = Signal(object)

    def handle_activity_received(self, twitch_event: TwitchEvent) -> None:
        self.activity_received.emit(twitch_event)

    def handle_notice_received(self, notice: TwitchChatNotice) -> None:
        self.notice_received.emit(notice)

    def handle_auth_changed(
        self,
        state: TwitchAuthState,
        detail: str,
    ) -> None:
        self.auth_changed.emit(state, detail)

    def handle_bot_auth_changed(
        self,
        state: TwitchAuthState,
        detail: str,
    ) -> None:
        self.bot_auth_changed.emit(state, detail)

    def handle_status_changed(
        self,
        state: TwitchConnectionState,
        channel: str,
    ) -> None:
        self.status_changed.emit(state, channel)

    def handle_message_received(self, chat_message: TwitchMessage) -> None:
        self.message_received.emit(chat_message)

    def handle_error(self, message: str) -> None:
        self.error_received.emit(message)

    def handle_diagnostic(
        self,
        diagnostic: TwitchEventDiagnostic,
    ) -> None:
        self.diagnostic_received.emit(diagnostic)
