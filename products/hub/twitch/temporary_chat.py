from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal, Slot
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from products.hub.config.twitch import TWITCH_CLIENT_ID
from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.live import TwitchEventSubSocket
from products.hub.twitch.models import TwitchMessage
from shared.streamhouse_runtime.logger import Logger


class TemporaryTwitchChatSession(QObject):
    """Runtime-only EventSub chat session for one non-primary channel."""

    message_received = Signal(object)
    state_changed = Signal(str)

    EVENTSUB_SUBSCRIPTIONS_URL = (
        "https://api.twitch.tv/helix/eventsub/subscriptions"
    )

    def __init__(
        self,
        target_user_id: str,
        token: TwitchToken,
        parent: QObject | None = None,
        *,
        socket_factory=TwitchEventSubSocket,
        network: QNetworkAccessManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.target_user_id = target_user_id.strip()
        self.token = token
        self._closed = False
        self._started = False
        self._subscription_reply: QNetworkReply | None = None
        self._network = network or QNetworkAccessManager(self)
        self._socket = socket_factory(
            on_welcome=self._subscribe,
            on_message=self._receive_message,
            on_notification=self._ignore_notification,
            on_diagnostic=self._ignore_diagnostic,
            on_revocation=self._revoked,
            on_error=self._socket_error,
            on_bus_event=None,
            parent=self,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        if self._started or self._closed:
            return
        if not self.target_user_id or not self.token.user_id:
            self.state_changed.emit("Target chat is unavailable.")
            return
        if "user:read:chat" not in set(self.token.scopes):
            self.state_changed.emit(
                "Reconnect Twitch with chat permission to view target chat."
            )
            return
        self._started = True
        self.state_changed.emit("Connecting to target chat…")
        self._socket.open()

    @Slot(str)
    def _subscribe(self, session_id: str) -> None:
        if self._closed or self._subscription_reply is not None:
            return
        body = QByteArray(
            json.dumps(
                {
                    "type": "channel.chat.message",
                    "version": "1",
                    "condition": {
                        "broadcaster_user_id": self.target_user_id,
                        "user_id": self.token.user_id,
                    },
                    "transport": {
                        "method": "websocket",
                        "session_id": session_id,
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        request = QNetworkRequest(QUrl(self.EVENTSUB_SUBSCRIPTIONS_URL))
        request.setTransferTimeout(15_000)
        request.setRawHeader(b"Client-Id", TWITCH_CLIENT_ID.encode("ascii"))
        request.setRawHeader(
            b"Authorization",
            f"Bearer {self.token.access_token}".encode("ascii"),
        )
        request.setHeader(
            QNetworkRequest.KnownHeaders.ContentTypeHeader,
            "application/json",
        )
        reply = self._network.post(request, body)
        self._subscription_reply = reply
        reply.finished.connect(self._subscription_finished)

    @Slot()
    def _subscription_finished(self) -> None:
        reply = self.sender()
        if reply is None:
            return
        if self._subscription_reply is reply:
            self._subscription_reply = None
        if self._closed:
            reply.deleteLater()
            return
        status = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        success = (
            reply.error() == QNetworkReply.NetworkError.NoError
            and int(status or 0) in (200, 202)
        )
        reply.deleteLater()
        if success:
            self.state_changed.emit("Target chat connected — read-only in V1.")
            return
        Logger.warning(
            "Raid Landing target chat subscription failed.",
            source="TWITCH",
        )
        self.state_changed.emit("Target chat couldn’t connect.")
        self._socket.close()

    @Slot(object)
    def _receive_message(self, message: object) -> None:
        if (
            self._closed
            or not isinstance(message, TwitchMessage)
            or message.broadcaster_user_id != self.target_user_id
        ):
            return
        # Content remains in memory and is never logged or emitted globally.
        self.message_received.emit(message)

    @staticmethod
    def _ignore_notification(_subscription_type: str, _payload: dict) -> None:
        return

    @staticmethod
    def _ignore_diagnostic(_diagnostic: object) -> None:
        return

    def _revoked(self, _status: str) -> None:
        if not self._closed:
            self.state_changed.emit("Target chat permission was revoked.")

    def _socket_error(self, _message: str) -> None:
        if not self._closed:
            Logger.warning(
                "Raid Landing target chat connection failed.",
                source="TWITCH",
            )
            self.state_changed.emit("Target chat connection was lost.")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        reply = self._subscription_reply
        self._subscription_reply = None
        if reply is not None and reply.isRunning():
            reply.abort()
        self._socket.close()
        self.state_changed.emit("Target chat closed.")
