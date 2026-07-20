from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from core.events import Events
from core.logger import Logger
from obs_service.models import ObsConnectionState, ObsEvent, ObsRequestResult


class ObsWebSocketService(QObject):
    """OBS WebSocket 5.x JSON client with authentication and reconnect."""

    state_changed = Signal(object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.socket = QWebSocket(parent=self)
        self.socket.textMessageReceived.connect(self._receive_text)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.errorOccurred.connect(self._socket_error)
        self.state = ObsConnectionState.DISCONNECTED
        self.host = "127.0.0.1"
        self.port = 4455
        self.password = ""
        self.auto_reconnect = True
        self._intentional_close = True
        self._identified = False
        self._callbacks: dict[str, Callable[[ObsRequestResult], None]] = {}
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setSingleShot(True)
        self.reconnect_timer.setInterval(2_000)
        self.reconnect_timer.timeout.connect(self.connect)

    def configure(
        self,
        host: str,
        port: int,
        password: str,
        *,
        auto_reconnect: bool = True,
    ) -> None:
        self.host = host.strip() or "127.0.0.1"
        self.port = min(max(int(port), 1), 65535)
        self.password = password
        self.auto_reconnect = bool(auto_reconnect)

    def connect(self) -> None:
        if self.socket.state() in {
            QAbstractSocket.SocketState.ConnectingState,
            QAbstractSocket.SocketState.ConnectedState,
        }:
            return
        self.reconnect_timer.stop()
        self._intentional_close = False
        self._identified = False
        self._set_state(ObsConnectionState.CONNECTING, f"{self.host}:{self.port}")
        self.socket.open(QUrl(f"ws://{self.host}:{self.port}"))

    def disconnect(self) -> None:
        self._intentional_close = True
        self.reconnect_timer.stop()
        was_connected = self._identified
        self._identified = False
        self._callbacks.clear()
        self.socket.close()
        if was_connected:
            self._publish_event("ConnectionClosed", {})
        self._set_state(ObsConnectionState.DISCONNECTED, "Disconnected")

    @property
    def connected(self) -> bool:
        return self.state is ObsConnectionState.CONNECTED and self._identified

    def send_request(
        self,
        request_type: str,
        request_data: dict[str, object] | None = None,
        callback: Callable[[ObsRequestResult], None] | None = None,
    ) -> str:
        if not self.connected:
            raise ValueError("OBS is not connected.")
        request_id = uuid4().hex
        if callback is not None:
            self._callbacks[request_id] = callback
        data: dict[str, object] = {
            "requestType": request_type,
            "requestId": request_id,
        }
        if request_data:
            data["requestData"] = request_data
        self.socket.sendTextMessage(json.dumps({"op": 6, "d": data}))
        return request_id

    def set_scene_item_enabled(
        self,
        scene_name: str,
        source_name: str,
        action: str,
    ) -> str:
        def found(result: ObsRequestResult) -> None:
            if not result.succeeded:
                return
            item_id = result.response_data.get("sceneItemId")
            if item_id is None:
                return
            if action == "toggle":
                self.send_request(
                    "GetSceneItemEnabled",
                    {"sceneName": scene_name, "sceneItemId": item_id},
                    lambda state: self._set_resolved_scene_item(
                        scene_name,
                        item_id,
                        not bool(state.response_data.get("sceneItemEnabled", False)),
                    ) if state.succeeded else None,
                )
            else:
                self._set_resolved_scene_item(
                    scene_name, item_id, action == "show"
                )

        return self.send_request(
            "GetSceneItemId",
            {"sceneName": scene_name, "sourceName": source_name},
            found,
        )

    def _set_resolved_scene_item(
        self, scene_name: str, item_id: object, enabled: bool
    ) -> None:
        self.send_request(
            "SetSceneItemEnabled",
            {
                "sceneName": scene_name,
                "sceneItemId": item_id,
                "sceneItemEnabled": enabled,
            },
        )

    @staticmethod
    def authentication(password: str, salt: str, challenge: str) -> str:
        secret = base64.b64encode(
            hashlib.sha256((password + salt).encode("utf-8")).digest()
        ).decode("ascii")
        return base64.b64encode(
            hashlib.sha256((secret + challenge).encode("utf-8")).digest()
        ).decode("ascii")

    def _receive_text(self, text: str) -> None:
        try:
            payload = json.loads(text)
            operation = int(payload.get("op", -1))
            data = payload.get("d", {})
            if not isinstance(data, dict):
                raise ValueError("OBS message data is invalid.")
            if operation == 0:
                self._identify(data)
            elif operation == 2:
                self.reconnect_timer.stop()
                self._identified = True
                self._set_state(ObsConnectionState.CONNECTED, f"{self.host}:{self.port}")
                self._publish_event("ConnectionOpened", {})
            elif operation == 5:
                event_data = data.get("eventData", {})
                self._publish_event(
                    str(data.get("eventType", "")),
                    event_data if isinstance(event_data, dict) else {},
                )
            elif operation == 7:
                self._request_response(data)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            Logger.warning(f"Ignored invalid OBS WebSocket message: {error}", source="OBS")

    def _identify(self, data: dict[str, object]) -> None:
        identify: dict[str, object] = {
            "rpcVersion": min(int(data.get("rpcVersion", 1)), 1),
            # All ordinary event categories, excluding the high-volume meters.
            "eventSubscriptions": (1 << 11) - 1,
        }
        authentication = data.get("authentication")
        if isinstance(authentication, dict):
            identify["authentication"] = self.authentication(
                self.password,
                str(authentication.get("salt", "")),
                str(authentication.get("challenge", "")),
            )
        self.socket.sendTextMessage(json.dumps({"op": 1, "d": identify}))

    def _request_response(self, data: dict[str, object]) -> None:
        status = data.get("requestStatus", {})
        response = data.get("responseData", {})
        if not isinstance(status, dict):
            status = {}
        if not isinstance(response, dict):
            response = {}
        result = ObsRequestResult(
            request_id=str(data.get("requestId", "")),
            request_type=str(data.get("requestType", "")),
            succeeded=bool(status.get("result", False)),
            code=int(status.get("code", 0)),
            comment=str(status.get("comment", "")),
            response_data=response,
        )
        callback = self._callbacks.pop(result.request_id, None)
        if callback is not None:
            callback(result)
        Events.emit("obs_request_completed", result=result)
        if not result.succeeded:
            Logger.warning(
                f'OBS request "{result.request_type}" failed: {result.comment or result.code}',
                source="OBS",
            )

    def _publish_event(self, event_type: str, event_data: dict[str, object]) -> None:
        if not event_type:
            return
        event = ObsEvent(event_type, dict(event_data))
        Events.emit("obs_event", obs_event=event)
        Events.emit(f"obs_event.{event_type}", obs_event=event)

    def _disconnected(self) -> None:
        was_connected = self._identified
        self._identified = False
        if was_connected:
            self._publish_event("ConnectionClosed", {})
        if self._intentional_close:
            self._set_state(ObsConnectionState.DISCONNECTED, "Disconnected")
            return
        if self.auto_reconnect:
            self._set_state(
                ObsConnectionState.CONNECTING,
                "Reconnecting automatically…",
            )
            self.reconnect_timer.start()
        else:
            self._set_state(ObsConnectionState.ERROR, "Connection lost")

    def _socket_error(self, _error: QAbstractSocket.SocketError) -> None:
        detail = self.socket.errorString() or "OBS connection failed"
        if self.auto_reconnect and not self._intentional_close:
            self._set_state(
                ObsConnectionState.CONNECTING,
                f"{detail} — retrying automatically…",
            )
        else:
            self._set_state(ObsConnectionState.ERROR, detail)

    def _set_state(self, state: ObsConnectionState, detail: str) -> None:
        if self.state is state and state is not ObsConnectionState.ERROR:
            return
        self.state = state
        self.state_changed.emit(state, detail)
        Events.emit("obs_status_changed", state=state, detail=detail)
