from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from uuid import uuid4

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from products.hub.automation.cancellation import current_cancellation
from products.hub.core.events import Events
from shared.streamhouse_runtime.logger import Logger
from products.hub.obs_service.models import ObsConnectionState, ObsEvent, ObsRequestResult


@dataclass(slots=True)
class _ObsRequestWaiter:
    request_type: str
    request_data: dict[str, object]
    completed: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)
    request_id: str = ""
    result: ObsRequestResult | None = None
    cancelled: bool = False
    event_loop: QEventLoop | None = None

    def finish(self, result: ObsRequestResult) -> bool:
        with self.lock:
            if self.cancelled or self.result is not None:
                return False
            self.result = result
            self.completed.set()
            loop = self.event_loop
        if loop is not None:
            loop.quit()
        return True

    def timeout(self, result: ObsRequestResult) -> str:
        with self.lock:
            if self.result is not None:
                return ""
            self.cancelled = True
            self.result = result
            self.completed.set()
            request_id = self.request_id
            loop = self.event_loop
        if loop is not None:
            loop.quit()
        return request_id


class ObsWebSocketService(QObject):
    """OBS WebSocket 5.x JSON client with authentication and reconnect."""

    DEFAULT_AUTOMATION_TIMEOUT_MS = 10_000
    state_changed = Signal(object, str)
    _waiter_requested = Signal(object)
    _request_cancelled = Signal(str)

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
        self._automation_requests_stopping = False
        self._callbacks: dict[str, Callable[[ObsRequestResult], None]] = {}
        self._request_types: dict[str, str] = {}
        self._input_mute_states: dict[str, bool] = {}
        self._primary_audio_input = ""
        self._current_program_scene = ""
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setSingleShot(True)
        self.reconnect_timer.setInterval(2_000)
        self.reconnect_timer.timeout.connect(self._retry_connect)
        self._waiter_requested.connect(
            self._dispatch_waiter,
            Qt.ConnectionType.QueuedConnection,
        )
        self._request_cancelled.connect(
            self._cancel_request,
            Qt.ConnectionType.QueuedConnection,
        )

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

    def connect(self, *, silent: bool = False) -> None:
        if self.socket.state() in {
            QAbstractSocket.SocketState.ConnectingState,
            QAbstractSocket.SocketState.ConnectedState,
        }:
            return
        self.reconnect_timer.stop()
        self._intentional_close = False
        self._identified = False
        if not silent:
            self._set_state(ObsConnectionState.CONNECTING, f"{self.host}:{self.port}")
        self.socket.open(QUrl(f"ws://{self.host}:{self.port}"))

    def _retry_connect(self) -> None:
        self.connect(silent=True)

    def disconnect(self) -> None:
        self._intentional_close = True
        self.reconnect_timer.stop()
        was_connected = self._identified
        self._identified = False
        self.cancel_pending_requests("OBS disconnected before the request completed.")
        self.socket.close()
        if was_connected:
            self._publish_event("ConnectionClosed", {})
        self._set_state(ObsConnectionState.DISCONNECTED, "Disconnected")

    @property
    def connected(self) -> bool:
        return self.state is ObsConnectionState.CONNECTED and self._identified

    @property
    def current_program_scene(self) -> str:
        return self._current_program_scene

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
            self._request_types[request_id] = request_type
        data: dict[str, object] = {
            "requestType": request_type,
            "requestId": request_id,
        }
        if request_data:
            data["requestData"] = request_data
        self.socket.sendTextMessage(json.dumps({"op": 6, "d": data}))
        return request_id

    def request_and_wait(
        self,
        request_type: str,
        request_data: dict[str, object] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> ObsRequestResult:
        """Run one OBS request on the socket's Qt thread and await its response.

        Qt-thread callers use a nested event loop so Hub remains responsive.
        Worker callers block only their worker thread while the request is
        marshalled to this object's owning Qt thread.
        """
        clean_type = str(request_type).strip()
        if not clean_type:
            return self._failure("", "OBS request type cannot be blank.")
        if self._automation_requests_stopping:
            return self._failure(clean_type, "Hub is shutting down.")
        timeout = max(
            int(
                self.DEFAULT_AUTOMATION_TIMEOUT_MS
                if timeout_ms is None
                else timeout_ms
            ),
            1,
        )
        waiter = _ObsRequestWaiter(clean_type, dict(request_data or {}))
        cancellation = current_cancellation()
        remove_cancellation_callback = (
            cancellation.add_callback(
                lambda reason: self._cancel_waiter(waiter, reason)
            )
            if cancellation is not None
            else lambda: None
        )
        try:
            if QThread.currentThread() == self.thread():
                loop = QEventLoop()
                waiter.event_loop = loop
                self._dispatch_waiter(waiter)
                if not waiter.completed.is_set():
                    timer = QTimer()
                    timer.setSingleShot(True)
                    timer.timeout.connect(
                        lambda: self._timeout_waiter(waiter, timeout)
                    )
                    timer.start(timeout)
                    loop.exec()
                    timer.stop()
            else:
                self._waiter_requested.emit(waiter)
                if not waiter.completed.wait(timeout / 1000):
                    self._timeout_waiter(waiter, timeout)
        finally:
            remove_cancellation_callback()
        return waiter.result or self._failure(
            clean_type,
            "OBS request ended without a result.",
            request_id=waiter.request_id,
        )

    def _dispatch_waiter(self, waiter: _ObsRequestWaiter) -> None:
        with waiter.lock:
            if waiter.cancelled or waiter.result is not None:
                return
        try:
            request_id = self.send_request(
                waiter.request_type,
                waiter.request_data,
                waiter.finish,
            )
        except (TypeError, ValueError) as error:
            waiter.finish(self._failure(waiter.request_type, str(error)))
            return
        with waiter.lock:
            waiter.request_id = request_id
            cancelled = waiter.cancelled
        if cancelled:
            self._cancel_request(request_id)

    def _timeout_waiter(self, waiter: _ObsRequestWaiter, timeout_ms: int) -> None:
        request_id = waiter.timeout(
            self._failure(
                waiter.request_type,
                f"Timed out waiting {timeout_ms / 1000:g} seconds for OBS.",
                request_id=waiter.request_id,
            )
        )
        if request_id:
            if QThread.currentThread() == self.thread():
                self._cancel_request(request_id)
            else:
                self._request_cancelled.emit(request_id)

    def _cancel_waiter(self, waiter: _ObsRequestWaiter, detail: str) -> None:
        request_id = waiter.timeout(
            self._failure(
                waiter.request_type,
                detail,
                request_id=waiter.request_id,
            )
        )
        if request_id:
            if QThread.currentThread() == self.thread():
                self._cancel_request(request_id)
            else:
                self._request_cancelled.emit(request_id)

    def _cancel_request(self, request_id: str) -> None:
        self._callbacks.pop(request_id, None)
        self._request_types.pop(request_id, None)

    def cancel_pending_requests(
        self,
        detail: str = "OBS request cancelled.",
        *,
        stop_new: bool = False,
    ) -> None:
        if stop_new:
            self._automation_requests_stopping = True
        pending = tuple(self._callbacks.items())
        request_types = dict(self._request_types)
        self._callbacks.clear()
        self._request_types.clear()
        for request_id, callback in pending:
            callback(
                self._failure(
                    request_types.get(request_id, "OBS"),
                    detail,
                    request_id=request_id,
                )
            )

    @staticmethod
    def _failure(
        request_type: str,
        detail: str,
        *,
        request_id: str = "",
        code: int = -1,
    ) -> ObsRequestResult:
        return ObsRequestResult(
            request_id=request_id,
            request_type=request_type,
            succeeded=False,
            code=code,
            comment=detail,
        )

    def set_scene_item_enabled(
        self,
        scene_name: str,
        source_name: str,
        action: str,
        *,
        timeout_ms: int | None = None,
    ) -> ObsRequestResult:
        found = self.request_and_wait(
            "GetSceneItemId",
            {"sceneName": scene_name, "sourceName": source_name},
            timeout_ms=timeout_ms,
        )
        if not found.succeeded:
            return found
        item_id = found.response_data.get("sceneItemId")
        if item_id is None:
            return self._failure(
                "GetSceneItemId",
                f'OBS did not return an item ID for source "{source_name}".',
                request_id=found.request_id,
            )
        enabled = action == "show"
        if action == "toggle":
            state = self.request_and_wait(
                "GetSceneItemEnabled",
                {"sceneName": scene_name, "sceneItemId": item_id},
                timeout_ms=timeout_ms,
            )
            if not state.succeeded:
                return state
            enabled = not bool(
                state.response_data.get("sceneItemEnabled", False)
            )
        return self.request_and_wait(
            "SetSceneItemEnabled",
            {
                "sceneName": scene_name,
                "sceneItemId": item_id,
                "sceneItemEnabled": enabled,
            },
            timeout_ms=timeout_ms,
        )

    def set_source_filter_enabled(
        self,
        source_name: str,
        filter_name: str,
        action: str,
        *,
        timeout_ms: int | None = None,
    ) -> ObsRequestResult:
        if action == "toggle":
            result = self.request_and_wait(
                "GetSourceFilter",
                {"sourceName": source_name, "filterName": filter_name},
                timeout_ms=timeout_ms,
            )
            if not result.succeeded:
                return result
            enabled = not bool(result.response_data.get("filterEnabled", False))
        else:
            enabled = action == "enable"
        return self.request_and_wait(
            "SetSourceFilterEnabled",
            {
                "sourceName": source_name,
                "filterName": filter_name,
                "filterEnabled": enabled,
            },
            timeout_ms=timeout_ms,
        )

    def current_mute_state(
        self,
        input_name: str = "",
        *,
        timeout_ms: int = 1500,
    ) -> tuple[str, bool] | None:
        """Read an OBS input's current mute state for task variables."""
        if not self.connected:
            return None
        name = input_name.strip()
        if not name or name == "--":
            name = self._primary_audio_input
        if not name:
            inputs_result = self._request_sync(
                "GetInputList",
                timeout_ms=timeout_ms,
            )
            if inputs_result is None or not inputs_result.succeeded:
                return None
            raw_inputs = inputs_result.response_data.get("inputs", [])
            inputs = [
                value
                for value in raw_inputs
                if isinstance(value, dict)
                and str(value.get("inputName", "")).strip()
            ] if isinstance(raw_inputs, list) else []
            if not inputs:
                return None

            def priority(value: dict[str, object]) -> tuple[int, str]:
                candidate = str(value.get("inputName", "")).strip()
                kind = str(value.get("inputKind", "")).casefold()
                lowered = candidate.casefold()
                if "mic" in lowered or "microphone" in lowered:
                    rank = 0
                elif "input_capture" in kind and "output" not in kind:
                    rank = 1
                elif "audio" in lowered:
                    rank = 2
                else:
                    rank = 3
                return rank, lowered

            name = str(min(inputs, key=priority).get("inputName", "")).strip()
            self._primary_audio_input = name
        mute_result = self._request_sync(
            "GetInputMute",
            {"inputName": name},
            timeout_ms=timeout_ms,
        )
        if mute_result is None or not mute_result.succeeded:
            return None
        muted = bool(mute_result.response_data.get("inputMuted", False))
        self._input_mute_states[name] = muted
        self._primary_audio_input = name
        return name, muted

    def _request_sync(
        self,
        request_type: str,
        request_data: dict[str, object] | None = None,
        *,
        timeout_ms: int = 1500,
    ) -> ObsRequestResult | None:
        return self.request_and_wait(
            request_type,
            request_data,
            timeout_ms=timeout_ms,
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
                self.send_request(
                    "GetCurrentProgramScene",
                    callback=self._capture_current_program_scene,
                )
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
        self._request_types.pop(result.request_id, None)
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
        if event_type == "InputMuteStateChanged":
            input_name = str(event_data.get("inputName", "")).strip()
            if input_name and isinstance(event_data.get("inputMuted"), bool):
                self._input_mute_states[input_name] = bool(
                    event_data["inputMuted"]
                )
                self._primary_audio_input = input_name
        elif event_type == "CurrentProgramSceneChanged":
            self._current_program_scene = str(
                event_data.get("sceneName", "")
            ).strip()
        event = ObsEvent(event_type, dict(event_data))
        Events.emit("obs_event", obs_event=event)
        Events.emit(f"obs_event.{event_type}", obs_event=event)

    def _capture_current_program_scene(self, result: ObsRequestResult) -> None:
        if result.succeeded:
            self._current_program_scene = str(
                result.response_data.get("currentProgramSceneName", "")
            ).strip()

    def _disconnected(self) -> None:
        was_connected = self._identified
        self._identified = False
        self.cancel_pending_requests("OBS disconnected before the request completed.")
        if was_connected:
            self._publish_event("ConnectionClosed", {})
        if self._intentional_close:
            self._set_state(ObsConnectionState.DISCONNECTED, "Disconnected")
            return
        if self.auto_reconnect:
            self._set_state(
                ObsConnectionState.DISCONNECTED,
                "Waiting for OBS to open.",
            )
            self.reconnect_timer.start()
        else:
            self._set_state(ObsConnectionState.ERROR, "Connection lost")

    def _socket_error(self, _error: QAbstractSocket.SocketError) -> None:
        detail = self.socket.errorString() or "OBS connection failed"
        self.cancel_pending_requests(f"OBS connection failed: {detail}")
        if self.auto_reconnect and not self._intentional_close:
            self._set_state(
                ObsConnectionState.DISCONNECTED,
                f"{detail}. Waiting for OBS to open.",
            )
            if not self.reconnect_timer.isActive():
                self.reconnect_timer.start()
        else:
            self._set_state(ObsConnectionState.ERROR, detail)

    def _set_state(self, state: ObsConnectionState, detail: str) -> None:
        if self.state is state and state is not ObsConnectionState.ERROR:
            return
        self.state = state
        self.state_changed.emit(state, detail)
        Events.emit("obs_status_changed", state=state, detail=detail)
