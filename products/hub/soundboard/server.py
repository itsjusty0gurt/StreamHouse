from __future__ import annotations

import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Signal

from shared.streamhouse_runtime.logger import Logger
from products.hub.core.resources import repository_resource_path
from products.hub.soundboard.store import SoundboardStore


class SoundboardLocalServer(QObject):
    """Serve the real Extension viewer locally and bridge it to routines."""

    trigger_requested = Signal(str, str, dict)
    STATIC_FILES = {
        "/": ("viewer.html", "text/html; charset=utf-8"),
        "/viewer.html": ("viewer.html", "text/html; charset=utf-8"),
        "/viewer.css": ("viewer.css", "text/css; charset=utf-8"),
        "/viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
        "/config.js": ("config.js", "text/javascript; charset=utf-8"),
        "/config.html": ("config.html", "text/html; charset=utf-8"),
    }

    def __init__(self, store: SoundboardStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.token = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._last_trigger: dict[str, float] = {}
        self.asset_root = repository_resource_path("extensions/twitch/app")

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        if self._server is None:
            return ""
        return (
            f"http://127.0.0.1:{self._server.server_port}/"
            f"?token={self.token}"
        )

    def start(self, port: int = 0) -> str:
        if self._server is not None:
            return self.url
        self._validate_assets()
        owner = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                static = owner.STATIC_FILES.get(parsed.path)
                if static is not None:
                    filename, content_type = static
                    self._send_bytes(
                        (owner.asset_root / filename).read_bytes(),
                        content_type,
                    )
                    return
                if parsed.path == "/api/config":
                    token = parse_qs(parsed.query).get("token", [""])[0]
                    if not secrets.compare_digest(token, owner.token):
                        self._send_json(
                            {"error": "Access denied."}, HTTPStatus.FORBIDDEN
                        )
                        return
                    self._send_json(owner.store.public_config())
                    return
                self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                if urlparse(self.path).path != "/api/trigger":
                    self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    length = max(
                        0,
                        min(int(self.headers.get("Content-Length", "0")), 4096),
                    )
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(
                        {"error": "Invalid request."}, HTTPStatus.BAD_REQUEST
                    )
                    return
                if not isinstance(payload, dict) or not secrets.compare_digest(
                    str(payload.get("token", "")), owner.token
                ):
                    self._send_json(
                        {"error": "Access denied."}, HTTPStatus.FORBIDDEN
                    )
                    return
                button_id = str(payload.get("button_id", ""))
                found = owner.store.get_button(button_id)
                if found is None:
                    self._send_json(
                        {"error": "Sound not found."}, HTTPStatus.NOT_FOUND
                    )
                    return
                _page, button = found
                if not button.enabled or not button.routine_id:
                    self._send_json(
                        {"error": "Sound is unavailable."}, HTTPStatus.CONFLICT
                    )
                    return
                now = monotonic()
                if now - owner._last_trigger.get(button_id, 0.0) < 0.25:
                    self._send_json(
                        {"error": "Please wait."}, HTTPStatus.TOO_MANY_REQUESTS
                    )
                    return
                owner._last_trigger[button_id] = now
                viewer = " ".join(
                    str(payload.get("viewer", "Local Viewer")).split()
                )[:50]
                context = {
                    "user": viewer or "Local Viewer",
                    "soundboard_button": button.label,
                    "soundboard_button_id": button.button_id,
                }
                owner.trigger_requested.emit(
                    button.button_id,
                    button.routine_id,
                    context,
                )
                self._send_json({"accepted": True}, HTTPStatus.ACCEPTED)

            def _send_json(
                self,
                payload: object,
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                self._send_bytes(
                    json.dumps(payload).encode("utf-8"),
                    "application/json; charset=utf-8",
                    status,
                )

            def _send_bytes(
                self,
                payload: bytes,
                content_type: str,
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                self.send_response(int(status))
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(
            ("127.0.0.1", int(port)), RequestHandler
        )
        self._server.daemon_threads = True
        self._thread = Thread(
            target=self._server.serve_forever,
            name="StreamhouseSoundboardPreview",
            daemon=True,
        )
        self._thread.start()
        Logger.info(
            "Local soundboard preview started at "
            f"http://127.0.0.1:{self._server.server_port}/",
            source="TWITCH",
        )
        return self.url

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self._last_trigger.clear()
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)
        Logger.info("Local soundboard preview stopped.", source="TWITCH")

    def _validate_assets(self) -> None:
        missing = [
            filename
            for filename, _content_type in self.STATIC_FILES.values()
            if not (self.asset_root / filename).is_file()
        ]
        if missing:
            raise OSError(
                "Soundboard viewer assets are missing: "
                + ", ".join(sorted(set(missing)))
            )
