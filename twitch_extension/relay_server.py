"""Minimal hosted backend for the Sally Twitch soundboard Extension.

Run behind an HTTPS reverse proxy. Required environment variables:

TWITCH_EXTENSION_SECRET  Base64 Extension secret from the Twitch console.
SALLY_RELAY_KEYS         JSON object mapping channel IDs to Sally relay keys.
SALLY_RELAY_DB           Optional SQLite path (defaults to relay.sqlite3).
PORT                     Optional listening port (defaults to 8080).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_hex
from threading import RLock
from urllib.parse import urlparse


SUPPORT_EMAIL = "xxitsjusty0gurtxx@gmail.com"


def _legal_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - Sally Soundboard</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; background: #18181b; color: #efeff1; line-height: 1.6; }}
    main {{ width: min(760px, calc(100% - 32px)); margin: 48px auto; }}
    h1, h2 {{ color: #00d47b; }}
    a {{ color: #7ee8b7; }}
    .updated {{ color: #adadb8; }}
  </style>
</head>
<body><main><h1>{title}</h1>{body}</main></body>
</html>"""


PRIVACY_PAGE = _legal_page(
    "Privacy Policy",
    f"""
<p class="updated">Effective July 21, 2026</p>
<p>Sally Soundboard lets Twitch viewers request sounds configured by a broadcaster
running Sally AI Bot.</p>
<h2>Information processed</h2>
<p>The Extension receives a Twitch-signed authorization token containing the
broadcaster channel ID, an opaque viewer identifier, and the viewer role. The
opaque identifier is used temporarily to enforce a short rate limit and is not
stored in the relay database. The relay stores the broadcaster's public button
configuration and pending requests containing a random event ID, button ID,
viewer role, and timestamp.</p>
<h2>Retention and sharing</h2>
<p>Pending requests expire after five minutes or are deleted as soon as Sally
acknowledges them. We do not sell personal information, use advertising trackers,
or request a viewer's linked Twitch identity. Data is processed by Twitch and by
our hosting provider, Render, only as needed to provide and secure the service.
Infrastructure logs may temporarily include standard request information such as
IP addresses.</p>
<h2>Your choices</h2>
<p>Using a sound button is optional. You may stop using the Extension at any time.
Broadcasters can deactivate the Extension and disconnect Sally from the relay.</p>
<h2>Contact</h2>
<p>Questions or privacy requests may be sent to
<a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
""",
)


TERMS_PAGE = _legal_page(
    "Terms of Service",
    f"""
<p class="updated">Effective July 21, 2026</p>
<p>By using Sally Soundboard, you agree to these terms and Twitch's applicable
terms and policies.</p>
<h2>Using the Extension</h2>
<p>Sally Soundboard sends a viewer's selected button request to the broadcaster's
Sally AI Bot installation. The broadcaster chooses every available button,
routine, audio file, volume, and resulting stream action. Do not use the Extension
to harass others, disrupt a service, evade rate limits, or trigger unlawful,
harmful, or rights-infringing content.</p>
<h2>Availability</h2>
<p>The Extension is provided as-is and may be changed, interrupted, rate-limited,
or discontinued. Sound playback is not guaranteed because it depends on Twitch,
the hosted relay, the broadcaster's computer, and the broadcaster's configuration.</p>
<h2>Responsibility</h2>
<p>Viewers are responsible for their use of the Extension. Broadcasters are
responsible for the sounds and automation routines they make available and for
complying with Twitch rules and applicable law.</p>
<h2>Contact</h2>
<p>Questions may be sent to
<a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
""",
)


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class RelayState:
    def __init__(self) -> None:
        encoded_secret = os.environ.get("TWITCH_EXTENSION_SECRET", "").strip()
        if not encoded_secret:
            raise RuntimeError("TWITCH_EXTENSION_SECRET is required.")
        try:
            self.extension_secret = base64.b64decode(
                encoded_secret + "=" * (-len(encoded_secret) % 4),
                validate=True,
            )
        except (ValueError, binascii.Error) as error:
            raise RuntimeError("TWITCH_EXTENSION_SECRET is not valid base64.") from error
        try:
            keys = json.loads(os.environ.get("SALLY_RELAY_KEYS", "{}"))
        except json.JSONDecodeError as error:
            raise RuntimeError("SALLY_RELAY_KEYS must be a JSON object.") from error
        if not isinstance(keys, dict):
            raise RuntimeError("SALLY_RELAY_KEYS must be a JSON object.")
        self.relay_keys = {str(channel): str(key) for channel, key in keys.items()}
        database_path = Path(os.environ.get("SALLY_RELAY_DB", "relay.sqlite3"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database = sqlite3.connect(database_path, check_same_thread=False)
        self.database.row_factory = sqlite3.Row
        self.lock = RLock()
        self.rate_limits: dict[str, float] = {}
        with self.database:
            self.database.execute(
                "CREATE TABLE IF NOT EXISTS configs ("
                "channel_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            self.database.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "event_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, "
                "button_id TEXT NOT NULL, viewer_role TEXT NOT NULL, created_at REAL NOT NULL)"
            )

    def verify_twitch_jwt(self, authorization: str) -> dict[str, object]:
        if not authorization.startswith("Bearer "):
            raise PermissionError("Missing Twitch Extension token.")
        token = authorization[7:].strip()
        parts = token.split(".")
        if len(parts) != 3:
            raise PermissionError("Invalid Twitch Extension token.")
        signed = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected = hmac.new(self.extension_secret, signed, hashlib.sha256).digest()
        try:
            supplied = _base64url_decode(parts[2])
            header = json.loads(_base64url_decode(parts[0]).decode("utf-8"))
            payload = json.loads(_base64url_decode(parts[1]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PermissionError("Invalid Twitch Extension token.") from error
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise PermissionError("Invalid Twitch Extension token.")
        if not hmac.compare_digest(expected, supplied) or header.get("alg") != "HS256":
            raise PermissionError("Invalid Twitch Extension signature.")
        try:
            expires_at = float(payload.get("exp", 0))
        except (TypeError, ValueError) as error:
            raise PermissionError("Invalid Twitch Extension expiration.") from error
        if expires_at < time.time() - 30:
            raise PermissionError("Expired Twitch Extension token.")
        channel_id = str(payload.get("channel_id", ""))
        if not channel_id:
            raise PermissionError("Twitch Extension token omitted the channel.")
        return payload

    def verify_sally(self, channel_id: str, supplied_key: str) -> None:
        expected = self.relay_keys.get(channel_id, "")
        if not expected or not hmac.compare_digest(expected, supplied_key):
            raise PermissionError("Invalid Sally relay credentials.")

    def config(self, channel_id: str) -> dict[str, object]:
        with self.lock:
            row = self.database.execute(
                "SELECT payload FROM configs WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else {"version": 1, "pages": []}

    def save_config(self, channel_id: str, payload: object) -> None:
        clean = self._validate_config(payload)
        encoded = json.dumps(clean, separators=(",", ":"))
        with self.lock, self.database:
            self.database.execute(
                "INSERT INTO configs(channel_id, payload, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(channel_id) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (channel_id, encoded, time.time()),
            )

    def enqueue(self, claims: dict[str, object], button_id: str) -> str:
        channel_id = str(claims["channel_id"])
        config = self.config(channel_id)
        valid_buttons = {
            str(button.get("id", ""))
            for page in config.get("pages", [])
            if isinstance(page, dict)
            for button in page.get("buttons", [])
            if isinstance(button, dict)
        }
        if button_id not in valid_buttons:
            raise ValueError("That sound is not available.")
        viewer = str(claims.get("opaque_user_id", "anonymous"))
        rate_key = f"{channel_id}:{viewer}:{button_id}"
        now = time.time()
        with self.lock:
            if now - self.rate_limits.get(rate_key, 0.0) < 2.0:
                raise RuntimeError("Please wait before using that sound again.")
            self.rate_limits[rate_key] = now
            self._cleanup(now)
            event_id = token_hex(16)
            with self.database:
                self.database.execute(
                    "INSERT INTO events VALUES(?, ?, ?, ?, ?)",
                    (
                        event_id,
                        channel_id,
                        button_id,
                        str(claims.get("role", "viewer"))[:20],
                        now,
                    ),
                )
        return event_id

    def poll(self, channel_id: str) -> list[dict[str, object]]:
        with self.lock:
            self._cleanup(time.time())
            rows = self.database.execute(
                "SELECT event_id, button_id, viewer_role, created_at FROM events "
                "WHERE channel_id = ? ORDER BY created_at LIMIT 20",
                (channel_id,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "button_id": row["button_id"],
                "viewer_id": "Extension Viewer",
                "viewer_role": row["viewer_role"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def acknowledge(self, channel_id: str, event_ids: list[str]) -> None:
        clean_ids = [str(value) for value in event_ids[:50] if str(value)]
        if not clean_ids:
            return
        placeholders = ",".join("?" for _value in clean_ids)
        with self.lock, self.database:
            self.database.execute(
                f"DELETE FROM events WHERE channel_id = ? AND event_id IN ({placeholders})",
                (channel_id, *clean_ids),
            )

    def _cleanup(self, now: float) -> None:
        with self.database:
            self.database.execute(
                "DELETE FROM events WHERE created_at < ?", (now - 300,)
            )
        if len(self.rate_limits) > 10_000:
            self.rate_limits = {
                key: timestamp
                for key, timestamp in self.rate_limits.items()
                if timestamp > now - 60
            }

    @staticmethod
    def _validate_config(payload: object) -> dict[str, object]:
        if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
            raise ValueError("Soundboard configuration is invalid.")
        pages: list[dict[str, object]] = []
        for raw_page in payload["pages"][:20]:
            if not isinstance(raw_page, dict):
                continue
            raw_buttons = raw_page.get("buttons", [])
            if not isinstance(raw_buttons, list):
                raw_buttons = []
            buttons = [
                {
                    "id": str(button.get("id", ""))[:64],
                    "label": str(button.get("label", "Sound"))[:60],
                }
                for button in raw_buttons[:9]
                if isinstance(button, dict) and str(button.get("id", ""))
            ]
            pages.append(
                {
                    "id": str(raw_page.get("id", ""))[:64],
                    "name": str(raw_page.get("name", "Sounds"))[:60],
                    "buttons": buttons,
                }
            )
        return {"version": 1, "pages": pages}


class RelayHandler(BaseHTTPRequestHandler):
    state: RelayState

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json({}, HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._send_json({"status": "ok"})
                return
            if path == "/privacy":
                self._send_html(PRIVACY_PAGE)
                return
            if path == "/terms":
                self._send_html(TERMS_PAGE)
                return
            if path == "/api/config":
                claims = self.state.verify_twitch_jwt(
                    self.headers.get("Authorization", "")
                )
                self._send_json(self.state.config(str(claims["channel_id"])))
                return
            if path == "/api/sally/poll":
                channel_id = self._verify_sally()
                self._send_json({"events": self.state.poll(channel_id)})
                return
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except PermissionError as error:
            self._send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)

    def do_PUT(self) -> None:  # noqa: N802
        try:
            if urlparse(self.path).path != "/api/sally/config":
                self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
                return
            channel_id = self._verify_sally()
            self.state.save_config(channel_id, self._read_json())
            self._send_json({"saved": True})
        except PermissionError as error:
            self._send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)
        except ValueError as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/trigger":
                claims = self.state.verify_twitch_jwt(
                    self.headers.get("Authorization", "")
                )
                payload = self._read_json()
                event_id = self.state.enqueue(claims, str(payload.get("button_id", "")))
                self._send_json({"accepted": True, "event_id": event_id}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/sally/ack":
                channel_id = self._verify_sally()
                payload = self._read_json()
                event_ids = payload.get("event_ids", [])
                if not isinstance(event_ids, list):
                    raise ValueError("event_ids must be a list.")
                self.state.acknowledge(channel_id, event_ids)
                self._send_json({"acknowledged": True})
                return
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except PermissionError as error:
            self._send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)
        except RuntimeError as error:
            self._send_json({"error": str(error)}, HTTPStatus.TOO_MANY_REQUESTS)
        except ValueError as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _verify_sally(self) -> str:
        channel_id = self.headers.get("X-Sally-Channel", "").strip()
        self.state.verify_sally(channel_id, self.headers.get("X-Sally-Key", ""))
        return channel_id

    def _read_json(self) -> dict[str, object]:
        length = max(0, min(int(self.headers.get("Content-Length", "0")), 65_536))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = b"" if status is HTTPStatus.NO_CONTENT else json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        origin = self.headers.get("Origin", "")
        if origin.startswith("https://") and origin.endswith(".ext-twitch.tv"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_value: str, *args: object) -> None:
        print(f"[relay] {self.address_string()} {format_value % args}")


def main() -> None:
    RelayHandler.state = RelayState()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), RelayHandler)
    print(f"Sally soundboard relay listening on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
