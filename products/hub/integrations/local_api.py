from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from products.hub.automation.service import AutomationService
from shared.streamhouse_runtime.logger import Logger


HUB_INTEGRATION_HOST = "127.0.0.1"
HUB_INTEGRATION_PORT = 8766
HUB_INTEGRATION_PROTOCOL_VERSION = 1
HUB_INTEGRATION_PATH = "/streamhouse/integration/v1"
MAX_REQUEST_BYTES = 64 * 1024


class _LoopbackHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


@dataclass(slots=True)
class _RequestTicket:
    payload: dict[str, Any]
    completed: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


class HubIntegrationController(QObject):
    """Qt-thread owner for the narrow list/run-routine boundary."""

    request_received = Signal(object)

    def __init__(
        self,
        automation_service: AutomationService,
        *,
        server_factory: Callable[..., "LocalHubIntegrationServer"] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._automation_service = automation_service
        self._accepting = True
        self._server = (server_factory or LocalHubIntegrationServer)(self.dispatch)
        self.request_received.connect(
            self._handle_ticket,
            Qt.ConnectionType.QueuedConnection,
        )

    @property
    def running(self) -> bool:
        return self._server.running

    def start(self) -> bool:
        if not self._accepting:
            return False
        try:
            self._server.start()
        except OSError as error:
            Logger.warning(
                f"Local Hub integration API could not start: {error}",
                source="APP",
            )
            return False
        return True

    def shutdown(self) -> None:
        self._accepting = False
        self._server.stop()

    def dispatch(
        self,
        payload: dict[str, Any],
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        if not self._accepting:
            return _error("hub_shutting_down", "Hub is shutting down.")
        ticket = _RequestTicket(payload=dict(payload))
        if QThread.currentThread() is self.thread():
            self._handle_ticket(ticket)
        else:
            self.request_received.emit(ticket)
        if not ticket.completed.wait(timeout):
            return _error("hub_busy", "Hub did not accept the request in time.")
        return ticket.response or _error("request_failed", "The request failed.")

    @Slot(object)
    def _handle_ticket(self, ticket: _RequestTicket) -> None:
        try:
            ticket.response = self._handle_request(ticket.payload)
        except Exception as error:  # defensive protocol boundary
            Logger.error(
                f"Local Hub integration request failed: {error}",
                source="AUTOMATION",
            )
            ticket.response = _error("request_failed", "The request failed.")
        finally:
            ticket.completed.set()

    def _handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._accepting:
            return _error("hub_shutting_down", "Hub is shutting down.")
        if payload.get("protocol_version") != HUB_INTEGRATION_PROTOCOL_VERSION:
            return _error(
                "unsupported_protocol",
                "This integration protocol version is not supported.",
            )
        request_type = str(payload.get("type", "")).strip()
        store = self._automation_service.routine_store
        if request_type == "list_routines":
            groups = {group.group_id: group.name for group in store.groups}
            routines = [
                {
                    "id": routine.routine_id,
                    "name": routine.name,
                    "group": groups.get(routine.group_id, ""),
                }
                for routine in store.routines
                if routine.enabled
            ]
            routines.sort(
                key=lambda item: (
                    item["group"].casefold(),
                    item["name"].casefold(),
                    item["id"],
                )
            )
            return _response("routines", routines=routines)
        if request_type != "run_routine":
            return _error("malformed_request", "Unknown request type.")
        routine_id = str(payload.get("routine_id", "")).strip()
        if not routine_id:
            return _error("malformed_request", "A routine ID is required.")
        routine = store.get(routine_id)
        if routine is None:
            Logger.warning(
                f"Local integration rejected unknown routine {routine_id}.",
                source="AUTOMATION",
            )
            return _error("unknown_routine", "The selected routine no longer exists.")
        if not routine.enabled:
            return _error("routine_disabled", "The selected routine is disabled.")
        result = self._automation_service.run_integration_routine(
            routine_id,
            "touch_portal",
        )
        if result.succeeded:
            Logger.info(
                f"Touch Portal routine request accepted for {routine_id}.",
                source="AUTOMATION",
            )
            return _response("run_routine_result", success=True)
        detail = next(
            (item.detail for item in result.routine_results if item.detail),
            "The routine request was rejected.",
        )
        return _error("execution_rejected", detail)


class LocalHubIntegrationServer:
    """Versioned localhost-only JSON transport for Hub integration clients."""

    def __init__(
        self,
        dispatch: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        host: str = HUB_INTEGRATION_HOST,
        port: int = HUB_INTEGRATION_PORT,
    ) -> None:
        if host != HUB_INTEGRATION_HOST:
            raise ValueError("The Hub integration API is loopback-only.")
        self._dispatch = dispatch
        self._host = host
        self._port = port
        self._server: _LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        server = self._server
        return server.server_port if server is not None else self._port

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                if self.path != HUB_INTEGRATION_PATH:
                    self._send(404, _error("not_found", "Unknown endpoint."))
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    self._send(400, _error("malformed_request", "Invalid request size."))
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(400, _error("malformed_request", "Invalid JSON request."))
                    return
                if not isinstance(payload, dict):
                    self._send(400, _error("malformed_request", "Request must be an object."))
                    return
                response = owner._dispatch(payload)
                status = 200 if response.get("success") is not False else 400
                self._send(status, response)

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = _LoopbackHTTPServer((self._host, self._port), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="StreamhouseHubIntegration",
            daemon=True,
        )
        self._thread.start()
        Logger.info(
            f"Local Hub integration API listening on {self._host}:{server.server_port}.",
            source="APP",
        )

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        Logger.info("Local Hub integration API stopped.", source="APP")


def _response(response_type: str, **values: Any) -> dict[str, Any]:
    return {
        "protocol_version": HUB_INTEGRATION_PROTOCOL_VERSION,
        "type": response_type,
        "success": True,
        **values,
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "protocol_version": HUB_INTEGRATION_PROTOCOL_VERSION,
        "type": "error",
        "success": False,
        "error": {"code": code, "message": message},
    }
