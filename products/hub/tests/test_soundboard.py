import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import time
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from products.hub.soundboard.server import SoundboardLocalServer
from products.hub.soundboard.relay import (
    SoundboardRelayClient,
    SoundboardRelayConfig,
    SoundboardRelayConfigStore,
)
from products.hub.soundboard.store import SoundboardStore
from extensions.twitch.app.relay_server import (
    RelayHandler,
    RelayState,
    _WARNED_COMPATIBILITY_EVENTS,
)
from products.hub.ui.soundboard_page import SoundboardPageWidget


class LegacyOnlyRelayHandler(RelayHandler):
    """Test double matching the relay deployed before Streamhouse routes."""

    observed_headers: list[dict[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/streamhouse/poll":
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_PUT(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/streamhouse/config":
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        super().do_PUT()

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/streamhouse/ack":
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        super().do_POST()

    def _verify_hub(self) -> str:
        self.observed_headers.append(dict(self.headers.items()))
        return super()._verify_hub()


class SoundboardStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "soundboard.json"
        self.store = SoundboardStore(self.path)
        self.store.load()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_pages_and_buttons_persist_in_display_order(self) -> None:
        first_page = self.store.pages[0]
        self.store.rename_page(first_page.page_id, "Memes")
        first = self.store.add_button(first_page.page_id, "Air Horn", "routine-1")
        second = self.store.add_button(first_page.page_id, "Laugh", "routine-2")
        reactions = self.store.add_page("Reactions")
        self.store.move_button(second.button_id, -1)
        self.store.move_page(reactions.page_id, -1)

        loaded = SoundboardStore(self.path)
        loaded.load()

        self.assertEqual([page.name for page in loaded.pages], ["Reactions", "Memes"])
        self.assertEqual(
            [button.label for button in loaded.pages[1].buttons],
            ["Laugh", "Air Horn"],
        )
        self.assertEqual(loaded.pages[1].buttons[1].button_id, first.button_id)

    def test_page_is_limited_to_nine_buttons(self) -> None:
        page = self.store.pages[0]
        for index in range(9):
            self.store.add_button(page.page_id, f"Sound {index}", f"routine-{index}")

        with self.assertRaisesRegex(ValueError, "up to 9"):
            self.store.add_button(page.page_id, "Too many", "routine-10")

    def test_public_config_hides_routine_ids_and_unavailable_buttons(self) -> None:
        page = self.store.pages[0]
        self.store.add_button(page.page_id, "Ready", "routine-secret")
        self.store.add_button(page.page_id, "Not configured")
        hidden = self.store.add_button(page.page_id, "Disabled", "routine-hidden")
        self.store.update_button(hidden.button_id, enabled=False)

        encoded = json.dumps(self.store.public_config())

        self.assertIn("Ready", encoded)
        self.assertNotIn("routine-secret", encoded)
        self.assertNotIn("Not configured", encoded)
        self.assertNotIn("Disabled", encoded)

    def test_adaptive_grid_dimensions(self) -> None:
        expected = {
            0: (1, 1),
            1: (1, 1),
            2: (1, 2),
            3: (2, 2),
            4: (2, 2),
            5: (2, 3),
            6: (2, 3),
            7: (3, 3),
            9: (3, 3),
        }
        for count, dimensions in expected.items():
            self.assertEqual(SoundboardPageWidget.grid_dimensions(count), dimensions)

    def test_hosted_relay_requires_https(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            SoundboardRelayConfig("http://example.com", "123").validate()
        SoundboardRelayConfig("https://relay.example.com", "123").validate()
        self.assertEqual(
            SoundboardRelayConfig().url,
            "https://streamhouse-soundboard-relay.onrender.com",
        )

    def test_hub_relay_base_environment_precedence(self) -> None:
        store = SoundboardRelayConfigStore(self.path.with_name("relay.json"))
        with (
            patch.object(store.secret_store, "load", return_value=""),
            patch("products.hub.soundboard.relay.Logger.warning"),
        ):
            with patch.dict(
                os.environ,
                {
                    "STREAMHOUSE_RELAY_BASE": "https://modern.example",
                    "SALLY_RELAY_BASE": "https://legacy.example",
                },
                clear=True,
            ):
                config, _key = store.load()
            self.assertEqual(config.url, "https://modern.example")

            with patch.dict(
                os.environ,
                {"SALLY_RELAY_BASE": "https://legacy.example"},
                clear=True,
            ):
                config, _key = store.load()
            self.assertEqual(config.url, "https://legacy.example")


class SoundboardServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = SoundboardStore(Path(self.directory.name) / "soundboard.json")
        self.store.load()
        page = self.store.pages[0]
        self.button = self.store.add_button(page.page_id, "Air Horn", "routine-1")
        self.server = SoundboardLocalServer(self.store)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.directory.cleanup()

    def test_config_requires_token_and_returns_viewer_safe_layout(self) -> None:
        base = self.server.url.split("?", 1)[0]
        with self.assertRaises(HTTPError) as denied:
            urlopen(base + "api/streamhouse/config", timeout=2)
        self.assertEqual(denied.exception.code, 403)

        response = urlopen(
            base + "api/streamhouse/config?token=" + self.server.token,
            timeout=2,
        )
        payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["pages"][0]["buttons"][0]["label"], "Air Horn")

    def test_local_preview_serves_the_twitch_extension_assets(self) -> None:
        base = self.server.url.split("?", 1)[0]
        html = urlopen(base, timeout=2).read().decode("utf-8")
        script = urlopen(base + "viewer.js", timeout=2).read().decode("utf-8")
        self.assertIn("viewer.js", html)
        self.assertIn("Twitch.ext.onAuthorized", script)

    def test_valid_button_request_emits_routine_trigger(self) -> None:
        spy = QSignalSpy(self.server.trigger_requested)
        payload = json.dumps(
            {
                "token": self.server.token,
                "button_id": self.button.button_id,
                "viewer": "Test Viewer",
            }
        ).encode("utf-8")
        request = Request(
            self.server.url.split("?", 1)[0] + "api/streamhouse/trigger",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        response = urlopen(request, timeout=2)

        self.assertEqual(response.status, 202)
        self.assertTrue(spy.wait(1000) or spy.count() == 1)
        arguments = spy.at(0)
        self.assertEqual(arguments[0], self.button.button_id)
        self.assertEqual(arguments[1], "routine-1")
        self.assertEqual(arguments[2]["user"], "Test Viewer")

    def test_invalid_token_cannot_trigger_sound(self) -> None:
        payload = json.dumps(
            {"token": "wrong", "button_id": self.button.button_id}
        ).encode("utf-8")
        request = Request(
            self.server.url.split("?", 1)[0] + "api/streamhouse/trigger",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(HTTPError) as denied:
            urlopen(request, timeout=2)
        self.assertEqual(denied.exception.code, 403)

    def test_local_preview_accepts_deployed_neutral_route_alias(self) -> None:
        base = self.server.url.split("?", 1)[0]
        with patch("products.hub.soundboard.server.Logger.warning") as warning:
            payload = json.loads(
                urlopen(
                    base + "api/config?token=" + self.server.token,
                    timeout=2,
                ).read()
            )
        self.assertIn("pages", payload)
        warning.assert_called_once()


class HostedRelayStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.secret = b"test-extension-secret"
        self.environment = patch.dict(
            os.environ,
            {
                "TWITCH_EXTENSION_SECRET": base64.b64encode(self.secret).decode("ascii"),
                "STREAMHOUSE_RELAY_KEYS": '{"123":"relay-key"}',
                "STREAMHOUSE_RELAY_DB": str(
                    Path(self.directory.name) / "relay.sqlite3"
                ),
            },
        )
        self.environment.start()
        self.state = RelayState()

    def tearDown(self) -> None:
        self.state.database.close()
        self.environment.stop()
        self.directory.cleanup()

    @staticmethod
    def _encode(value: object) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")

    def _jwt(self, **overrides: object) -> str:
        header = self._encode({"alg": "HS256", "typ": "JWT"})
        claims = {
            "channel_id": "123",
            "opaque_user_id": "U-test-viewer",
            "role": "viewer",
            "exp": time() + 60,
            **overrides,
        }
        payload = self._encode(claims)
        signature = base64.urlsafe_b64encode(
            hmac.new(
                self.secret,
                f"{header}.{payload}".encode("ascii"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii").rstrip("=")
        return f"{header}.{payload}.{signature}"

    def test_twitch_jwt_config_trigger_and_acknowledgment(self) -> None:
        claims = self.state.verify_twitch_jwt("Bearer " + self._jwt())
        self.state.verify_hub("123", "relay-key")
        self.state.save_config(
            "123",
            {
                "version": 1,
                "pages": [
                    {
                        "id": "page-1",
                        "name": "Memes",
                        "buttons": [{"id": "sound-1", "label": "Air Horn"}],
                    }
                ],
            },
        )

        event_id = self.state.enqueue(claims, "sound-1")
        events = self.state.poll("123")

        self.assertEqual(events[0]["event_id"], event_id)
        self.assertEqual(events[0]["button_id"], "sound-1")
        self.assertEqual(events[0]["viewer_id"], "Extension Viewer")
        self.state.acknowledge("123", [event_id])
        self.assertEqual(self.state.poll("123"), [])

    def test_invalid_twitch_signature_is_rejected(self) -> None:
        parts = self._jwt().split(".")
        signature = parts[2]
        parts[2] = ("A" if signature[0] != "A" else "B") + signature[1:]
        token = ".".join(parts)
        with self.assertRaises(PermissionError):
            self.state.verify_twitch_jwt("Bearer " + token)

    def test_expired_twitch_token_is_rejected(self) -> None:
        with self.assertRaisesRegex(PermissionError, "Expired"):
            self.state.verify_twitch_jwt("Bearer " + self._jwt(exp=time() - 60))

    def test_duplicate_viewer_trigger_remains_rate_limited(self) -> None:
        claims = self.state.verify_twitch_jwt("Bearer " + self._jwt())
        self.state.save_config(
            "123",
            {"pages": [{"id": "one", "buttons": [{"id": "sound-1"}]}]},
        )
        self.state.enqueue(claims, "sound-1")
        with self.assertRaisesRegex(RuntimeError, "wait"):
            self.state.enqueue(claims, "sound-1")

    def test_legacy_relay_environment_is_a_temporary_fallback(self) -> None:
        legacy_database = Path(self.directory.name) / "legacy-relay.sqlite3"
        _WARNED_COMPATIBILITY_EVENTS.clear()
        with self.assertLogs("streamhouse.relay", logging.WARNING) as captured:
            with patch.dict(
                os.environ,
                {
                    "TWITCH_EXTENSION_SECRET": base64.b64encode(
                        self.secret
                    ).decode("ascii"),
                    "SALLY_RELAY_KEYS": '{"456":"legacy-key"}',
                    "SALLY_RELAY_DB": str(legacy_database),
                },
                clear=True,
            ):
                legacy_state = RelayState()
        try:
            self.assertEqual(legacy_state.relay_keys, {"456": "legacy-key"})
            self.assertTrue(legacy_database.exists())
            messages = "\n".join(captured.output)
            self.assertIn("relay_compatibility_used", messages)
            self.assertNotIn("legacy-key", messages)
        finally:
            legacy_state.database.close()

    def test_conflicting_environment_uses_modern_without_logging_secrets(self) -> None:
        modern_database = Path(self.directory.name) / "modern.sqlite3"
        _WARNED_COMPATIBILITY_EVENTS.clear()
        with self.assertLogs("streamhouse.relay", logging.WARNING) as captured:
            with patch.dict(
                os.environ,
                {
                    "TWITCH_EXTENSION_SECRET": base64.b64encode(self.secret).decode(),
                    "STREAMHOUSE_RELAY_KEYS": '{"123":"modern-secret"}',
                    "SALLY_RELAY_KEYS": '{"123":"legacy-secret"}',
                    "STREAMHOUSE_RELAY_DB": str(modern_database),
                    "SALLY_RELAY_DB": str(Path(self.directory.name) / "legacy.sqlite3"),
                },
                clear=True,
            ):
                modern_state = RelayState()
        try:
            self.assertEqual(modern_state.relay_keys["123"], "modern-secret")
            messages = "\n".join(captured.output)
            self.assertIn(":conflict", messages)
            self.assertNotIn("modern-secret", messages)
            self.assertNotIn("legacy-secret", messages)
        finally:
            modern_state.database.close()

    def test_missing_required_relay_configuration_is_rejected(self) -> None:
        extension_secret = base64.b64encode(self.secret).decode("ascii")
        with patch.dict(
            os.environ,
            {"TWITCH_EXTENSION_SECRET": extension_secret},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "STREAMHOUSE_RELAY_KEYS"):
                RelayState()

        with patch.dict(
            os.environ,
            {
                "TWITCH_EXTENSION_SECRET": extension_secret,
                "STREAMHOUSE_RELAY_KEYS": '{"123":"key"}',
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "STREAMHOUSE_RELAY_DB"):
                RelayState()

    def test_database_configuration_rejects_a_directory(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TWITCH_EXTENSION_SECRET": base64.b64encode(self.secret).decode(),
                "STREAMHOUSE_RELAY_KEYS": '{"123":"key"}',
                "STREAMHOUSE_RELAY_DB": self.directory.name,
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "SQLite file path"):
                RelayState()


class SoundboardRelayClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.secret = b"client-test-secret"
        self.environment = patch.dict(
            os.environ,
            {
                "TWITCH_EXTENSION_SECRET": base64.b64encode(self.secret).decode("ascii"),
                "STREAMHOUSE_RELAY_KEYS": '{"123":"relay-key"}',
                "STREAMHOUSE_RELAY_DB": str(root / "relay.sqlite3"),
            },
        )
        self.environment.start()
        self.state = RelayState()
        RelayHandler.state = self.state
        self.http_server = ThreadingHTTPServer(("127.0.0.1", 0), RelayHandler)
        self.http_thread = Thread(target=self.http_server.serve_forever, daemon=True)
        self.http_thread.start()
        self.store = SoundboardStore(root / "soundboard.json")
        self.store.load()
        page = self.store.pages[0]
        self.button = self.store.add_button(page.page_id, "Air Horn", "routine-1")
        self.client = SoundboardRelayClient(self.store)

    def tearDown(self) -> None:
        self.client.disconnect_relay()
        self.http_server.shutdown()
        self.http_server.server_close()
        self.http_thread.join(timeout=2)
        self.state.database.close()
        self.environment.stop()
        self.directory.cleanup()

    def test_client_syncs_config_receives_trigger_and_acknowledges(self) -> None:
        status_spy = QSignalSpy(self.client.status_changed)
        trigger_spy = QSignalSpy(self.client.trigger_received)
        port = self.http_server.server_port
        self.client.connect_relay(
            SoundboardRelayConfig(f"http://127.0.0.1:{port}", "123"),
            "relay-key",
        )
        deadline = time() + 3
        while self.client.status != "Connected" and time() < deadline:
            QTest.qWait(20)
        self.assertEqual(self.client.status, "Connected")
        self.assertGreaterEqual(status_spy.count(), 1)
        self.assertEqual(
            self.state.config("123")["pages"][0]["buttons"][0]["label"],
            "Air Horn",
        )
        event_id = self.state.enqueue(
            {
                "channel_id": "123",
                "opaque_user_id": "U-viewer",
                "role": "viewer",
            },
            self.button.button_id,
        )

        deadline = time() + 3
        while trigger_spy.count() == 0 and time() < deadline:
            QTest.qWait(20)
        self.assertEqual(trigger_spy.count(), 1)
        arguments = trigger_spy.at(0)
        self.assertEqual(arguments[0], self.button.button_id)
        self.assertEqual(arguments[1]["soundboard_source"], "twitch_extension")
        deadline = time() + 2
        while self.state.poll("123") and time() < deadline:
            QTest.qWait(20)
        self.assertNotIn(
            event_id,
            [event["event_id"] for event in self.state.poll("123")],
        )

    def test_client_falls_back_to_pre_rebrand_relay(self) -> None:
        self.http_server.shutdown()
        self.http_server.server_close()
        self.http_thread.join(timeout=2)
        LegacyOnlyRelayHandler.state = self.state
        LegacyOnlyRelayHandler.observed_headers.clear()
        self.http_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            LegacyOnlyRelayHandler,
        )
        self.http_thread = Thread(
            target=self.http_server.serve_forever,
            daemon=True,
        )
        self.http_thread.start()

        self.client.connect_relay(
            SoundboardRelayConfig(
                f"http://127.0.0.1:{self.http_server.server_port}",
                "123",
            ),
            "relay-key",
        )
        deadline = time() + 3
        while self.client.status != "Connected" and time() < deadline:
            QTest.qWait(20)

        self.assertEqual(self.client.status, "Connected")
        self.assertTrue(self.client._legacy_routes)
        self.assertEqual(
            self.state.config("123")["pages"][0]["buttons"][0]["label"],
            "Air Horn",
        )
        self.assertTrue(LegacyOnlyRelayHandler.observed_headers)
        headers = LegacyOnlyRelayHandler.observed_headers[0]
        self.assertEqual(headers["X-Streamhouse-Channel"], "123")
        self.assertEqual(headers["X-Streamhouse-Key"], "relay-key")
        self.assertEqual(headers["X-Sally-Channel"], "123")
        self.assertEqual(headers["X-Sally-Key"], "relay-key")

    def test_legacy_route_and_headers_delegate_to_canonical_behavior(self) -> None:
        _WARNED_COMPATIBILITY_EVENTS.clear()
        base_url = f"http://127.0.0.1:{self.http_server.server_port}"
        modern = Request(
            base_url + "/api/streamhouse/poll",
            headers={
                "X-Streamhouse-Channel": "123",
                "X-Streamhouse-Key": "relay-key",
            },
        )
        legacy = Request(
            base_url + "/api/sally/poll",
            headers={"X-Sally-Channel": "123", "X-Sally-Key": "relay-key"},
        )
        modern_payload = json.loads(urlopen(modern, timeout=2).read())
        with self.assertLogs("streamhouse.relay", logging.WARNING) as captured:
            legacy_payload = json.loads(urlopen(legacy, timeout=2).read())
        self.assertEqual(legacy_payload, modern_payload)
        self.assertIn("relay_compatibility_used", "\n".join(captured.output))

        config_payload = json.dumps({"pages": []}).encode("utf-8")
        legacy_config = Request(
            base_url + "/api/sally/config",
            data=config_payload,
            method="PUT",
            headers={
                "Content-Type": "application/json",
                "X-Sally-Channel": "123",
                "X-Sally-Key": "relay-key",
            },
        )
        self.assertEqual(json.loads(urlopen(legacy_config, timeout=2).read()), {"saved": True})

        legacy_ack = Request(
            base_url + "/api/sally/ack",
            data=json.dumps({"event_ids": []}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Sally-Channel": "123",
                "X-Sally-Key": "relay-key",
            },
        )
        self.assertEqual(
            json.loads(urlopen(legacy_ack, timeout=2).read()),
            {"acknowledged": True},
        )

    def test_legacy_headers_work_on_modern_routes_through_compatibility_layer(self) -> None:
        _WARNED_COMPATIBILITY_EVENTS.clear()
        request = Request(
            f"http://127.0.0.1:{self.http_server.server_port}/api/streamhouse/poll",
            headers={"X-Sally-Channel": "123", "X-Sally-Key": "relay-key"},
        )
        with self.assertLogs("streamhouse.relay", logging.WARNING) as captured:
            payload = json.loads(urlopen(request, timeout=2).read())
        self.assertEqual(payload, {"events": []})
        self.assertIn("deprecated=X-Sally", "\n".join(captured.output))

    def test_conflicting_headers_use_modern_and_remain_strict(self) -> None:
        request = Request(
            f"http://127.0.0.1:{self.http_server.server_port}/api/streamhouse/poll",
            headers={
                "X-Streamhouse-Channel": "123",
                "X-Streamhouse-Key": "wrong-modern-key",
                "X-Sally-Channel": "123",
                "X-Sally-Key": "relay-key",
            },
        )
        _WARNED_COMPATIBILITY_EVENTS.clear()
        with self.assertLogs("streamhouse.relay", logging.WARNING) as captured:
            with self.assertRaises(HTTPError) as denied:
                urlopen(request, timeout=2)
        self.assertEqual(denied.exception.code, 403)
        error_payload = json.loads(denied.exception.read())
        self.assertIn("Streamhouse relay", error_payload["error"])
        self.assertNotIn("wrong-modern-key", "\n".join(captured.output))

    def test_default_modern_hostname_can_fall_back_during_transition(self) -> None:
        self.client.config = SoundboardRelayConfig(
            channel_id="123",
        )
        self.client.key = "relay-key"
        self.client._active_base_url = self.client.config.url
        with (
            patch.object(
                self.client,
                "_request_once",
                side_effect=[URLError("modern host unavailable"), {}],
            ) as request_once,
            patch("products.hub.soundboard.relay.Logger.warning"),
        ):
            self.assertEqual(self.client._request("/api/streamhouse/poll"), {})
        self.assertTrue(self.client._legacy_host_fallback)
        self.assertEqual(
            self.client._active_base_url,
            "https://sally-soundboard-relay.onrender.com",
        )
        self.assertEqual(request_once.call_count, 2)

    def test_missing_modern_hostname_falls_back_before_legacy_routes(self) -> None:
        self.client.config = SoundboardRelayConfig(channel_id="123")
        self.client.key = "relay-key"
        self.client._active_base_url = self.client.config.url
        not_found = HTTPError(
            self.client.config.url + "/api/streamhouse/poll",
            404,
            "Not Found",
            None,
            None,
        )
        with (
            patch.object(
                self.client,
                "_request_once",
                side_effect=[not_found, {}],
            ) as request_once,
            patch("products.hub.soundboard.relay.Logger.warning"),
        ):
            self.assertEqual(
                self.client._request(
                    "/api/streamhouse/poll",
                    legacy_path="/api/sally/poll",
                ),
                {},
            )
        self.assertTrue(self.client._legacy_host_fallback)
        self.assertFalse(self.client._legacy_routes)
        self.assertEqual(request_once.call_count, 2)

    def test_relay_serves_public_legal_pages(self) -> None:
        base_url = f"http://127.0.0.1:{self.http_server.server_port}"
        for path, heading in (
            ("/privacy", "Privacy Policy"),
            ("/terms", "Terms of Service"),
        ):
            with urlopen(base_url + path, timeout=2) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.headers.get_content_type(), "text/html"
                )
            self.assertIn(heading, body)
            self.assertIn("xxitsjusty0gurtxx@gmail.com", body)


if __name__ == "__main__":
    unittest.main()
