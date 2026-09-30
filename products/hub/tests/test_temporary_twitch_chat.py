from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication

from products.hub.core.events import Events
from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.temporary_chat import TemporaryTwitchChatSession


class _FakeSocket:
    def __init__(self, **callbacks) -> None:
        self.callbacks = callbacks
        self.open_count = 0
        self.close_count = 0

    def open(self) -> None:
        self.open_count += 1

    def close(self) -> None:
        self.close_count += 1


class _FakeReply(QObject):
    finished = Signal()

    def __init__(self, status: int = 202) -> None:
        super().__init__()
        self.status = status
        self.running = True
        self.aborted = False

    def error(self):
        return QNetworkReply.NetworkError.NoError

    def attribute(self, attribute):
        if attribute == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self.status
        return None

    def isRunning(self) -> bool:  # noqa: N802
        return self.running

    def abort(self) -> None:
        self.aborted = True
        self.running = False

    def complete(self) -> None:
        self.running = False
        self.finished.emit()


class _FakeNetwork(QObject):
    def __init__(self, reply: _FakeReply | None = None) -> None:
        super().__init__()
        self.reply = reply or _FakeReply()
        self.requests = []

    def post(self, request, body):
        self.requests.append((request, bytes(body)))
        return self.reply


class TemporaryTwitchChatSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _session(self, *, scopes: list[str] | None = None):
        token = TwitchToken(
            "access-token",
            "refresh-token",
            9_999_999_999,
            scopes if scopes is not None else ["user:read:chat"],
            user_id="chat-user",
        )
        network = _FakeNetwork()
        session = TemporaryTwitchChatSession(
            "target-user",
            token,
            socket_factory=_FakeSocket,
            network=network,
        )
        return session, network

    def test_uses_separate_socket_and_subscribes_only_to_target_chat(self) -> None:
        session, network = self._session()
        states = []
        session.state_changed.connect(states.append)

        session.start()
        self.assertEqual(session._socket.open_count, 1)
        session._socket.callbacks["on_welcome"]("temporary-session")

        self.assertEqual(len(network.requests), 1)
        request, raw_body = network.requests[0]
        self.assertEqual(
            request.url().toString(),
            TemporaryTwitchChatSession.EVENTSUB_SUBSCRIPTIONS_URL,
        )
        payload = json.loads(raw_body)
        self.assertEqual(payload["type"], "channel.chat.message")
        self.assertEqual(
            payload["condition"],
            {
                "broadcaster_user_id": "target-user",
                "user_id": "chat-user",
            },
        )
        self.assertEqual(
            payload["transport"],
            {"method": "websocket", "session_id": "temporary-session"},
        )
        network.reply.complete()
        self.assertIn("read-only", states[-1])

    def test_only_target_messages_are_exposed_and_never_emitted_globally(self) -> None:
        session, _network = self._session()
        received = []
        global_messages = []
        session.message_received.connect(received.append)
        Events.subscribe(
            "twitch_message_received",
            lambda chat_message: global_messages.append(chat_message),
        )
        target = TwitchMessage(
            "Viewer",
            "target text",
            datetime.now(timezone.utc),
            broadcaster_user_id="target-user",
        )
        other = TwitchMessage(
            "Viewer",
            "main text",
            datetime.now(timezone.utc),
            broadcaster_user_id="main-user",
        )

        session._socket.callbacks["on_message"](other)
        session._socket.callbacks["on_message"](target)

        self.assertEqual(received, [target])
        self.assertEqual(global_messages, [])
        Events.clear()

    def test_close_aborts_subscription_and_closes_only_temporary_socket(self) -> None:
        session, network = self._session()
        session.start()
        session._socket.callbacks["on_welcome"]("temporary-session")

        session.close()
        session.close()

        self.assertTrue(session.closed)
        self.assertTrue(network.reply.aborted)
        self.assertEqual(session._socket.close_count, 1)

    def test_missing_chat_scope_never_opens_socket(self) -> None:
        session, network = self._session(scopes=[])
        states = []
        session.state_changed.connect(states.append)

        session.start()

        self.assertEqual(session._socket.open_count, 0)
        self.assertEqual(network.requests, [])
        self.assertIn("permission", states[-1])


if __name__ == "__main__":
    unittest.main()
