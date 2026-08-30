from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, Signal

from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.paths import user_data_root
from shared.streamhouse_runtime.relay_config import (
    LEGACY_RELAY_BASE_DEFAULT,
    RELAY_COMPATIBILITY_REMOVE_AFTER,
    RELAY_COMPATIBILITY_VERSION,
    STREAMHOUSE_RELAY_BASE_DEFAULT,
    load_relay_environment,
)
from products.hub.core.secret_store import SecretStore
from products.hub.soundboard.store import SoundboardStore


_WARNED_RELAY_CONFIG_EVENTS: set[str] = set()


def _warn_relay_config_once(event: str, message: str) -> None:
    if event in _WARNED_RELAY_CONFIG_EVENTS:
        return
    _WARNED_RELAY_CONFIG_EVENTS.add(event)
    Logger.warning(message, source="TWITCH")


@dataclass(slots=True)
class SoundboardRelayConfig:
    url: str = STREAMHOUSE_RELAY_BASE_DEFAULT
    channel_id: str = ""
    auto_connect: bool = False

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> SoundboardRelayConfig:
        return cls(
            url=str(values.get("url", "")).strip().rstrip("/")[:500],
            channel_id=str(values.get("channel_id", "")).strip()[:50],
            auto_connect=bool(values.get("auto_connect", False)),
        )

    def validate(self) -> None:
        if not self.url.startswith("https://") and not self.url.startswith(
            ("http://127.0.0.1", "http://localhost")
        ):
            raise ValueError("The hosted relay must use HTTPS.")
        if not self.channel_id.isdigit():
            raise ValueError("Enter the broadcaster's numeric Twitch channel ID.")


class SoundboardRelayConfigStore:
    VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "twitch" / "soundboard-relay.json"
        self.secret_store = SecretStore(
            self.path.with_name("soundboard-relay-key.dat"),
            "Streamhouse soundboard relay key",
        )

    def load(self) -> tuple[SoundboardRelayConfig, str]:
        config = SoundboardRelayConfig()
        if self.path.exists():
            payload = load_json_with_backup(self.path)
            if not isinstance(payload, dict):
                raise ValueError("Soundboard relay settings must be a JSON object.")
            version = payload.get("version")
            if type(version) is not int or version != self.VERSION:
                raise ValueError(
                    f"Unsupported soundboard relay settings version {version}; "
                    f"expected {self.VERSION}."
                )
            config = SoundboardRelayConfig.from_dict(payload)
        selection = load_relay_environment(
            os.environ,
            base_default=config.url,
        ).base
        if selection.used_legacy:
            _warn_relay_config_once(
                "legacy-base-environment",
                "event=relay_compatibility_used "
                f"compatibility={RELAY_COMPATIBILITY_VERSION} "
                "deprecated=SALLY_RELAY_BASE "
                "replacement=STREAMHOUSE_RELAY_BASE "
                f"remove_after={RELAY_COMPATIBILITY_REMOVE_AFTER}",
            )
        elif selection.conflict:
            _warn_relay_config_once(
                "conflicting-base-environment",
                "event=relay_compatibility_conflict "
                f"compatibility={RELAY_COMPATIBILITY_VERSION} "
                "deprecated=STREAMHOUSE_RELAY_BASE+SALLY_RELAY_BASE:conflict "
                "replacement=STREAMHOUSE_RELAY_BASE:authoritative "
                f"remove_after={RELAY_COMPATIBILITY_REMOVE_AFTER}",
            )
        config.url = selection.value.rstrip("/")[:500]
        return config, self.secret_store.load()

    def save(self, config: SoundboardRelayConfig, key: str) -> None:
        atomic_write_json(self.path, {"version": self.VERSION, **asdict(config)})
        self.secret_store.save(key.strip())


class SoundboardRelayClient(QObject):
    """Maintain Hub's outbound connection to the hosted Extension relay."""

    status_changed = Signal(str)
    trigger_received = Signal(str, dict)

    def __init__(
        self,
        soundboard_store: SoundboardStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.soundboard_store = soundboard_store
        self.config = SoundboardRelayConfig()
        self.key = ""
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self._status = "Disconnected"
        self._legacy_routes = False
        self._active_base_url = STREAMHOUSE_RELAY_BASE_DEFAULT
        self._legacy_host_fallback = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def status(self) -> str:
        return self._status

    def connect_relay(self, config: SoundboardRelayConfig, key: str) -> None:
        config.validate()
        if not key.strip():
            raise ValueError("Enter the Streamhouse relay key.")
        self.disconnect_relay()
        self.config = config
        self.key = key.strip()
        self._legacy_routes = False
        self._active_base_url = config.url
        self._legacy_host_fallback = False
        self._stop.clear()
        self._wake.clear()
        self._set_status("Connecting")
        self._thread = Thread(
            target=self._run,
            name="StreamhouseSoundboardRelay",
            daemon=True,
        )
        self._thread.start()

    def disconnect_relay(self) -> None:
        thread = self._thread
        self._thread = None
        self._stop.set()
        self._wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._set_status("Disconnected")

    def request_sync(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        last_config = ""
        while not self._stop.is_set():
            try:
                current_config = json.dumps(
                    self.soundboard_store.public_config(),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if current_config != last_config:
                    self._put_config(current_config)
                    last_config = current_config
                events = self._poll()
                self._set_status("Connected")
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("event_id", ""))
                    button_id = str(event.get("button_id", ""))
                    if not event_id or not button_id:
                        continue
                    viewer_id = str(event.get("viewer_id", "Viewer"))
                    self.trigger_received.emit(
                        button_id,
                        {
                            "user": viewer_id,
                            "soundboard_button_id": button_id,
                            "soundboard_source": "twitch_extension",
                        },
                    )
                    self._ack(event_id)
                self._wake.wait(1.0)
                self._wake.clear()
            except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
                if self._stop.is_set():
                    break
                detail = getattr(error, "reason", None) or str(error)
                self._set_status(f"Waiting for relay: {detail}")
                self._wake.wait(5.0)
                self._wake.clear()

    def _request(
        self,
        path: str,
        *,
        legacy_path: str | None = None,
        method: str = "GET",
        payload: bytes | None = None,
    ) -> object:
        if self._legacy_routes and legacy_path:
            return self._request_once(
                legacy_path,
                method=method,
                payload=payload,
                include_legacy_headers=True,
            )
        try:
            return self._request_once(path, method=method, payload=payload)
        except HTTPError as error:
            if error.code != 404:
                raise
            if self._activate_legacy_host():
                return self._request(
                    path,
                    legacy_path=legacy_path,
                    method=method,
                    payload=payload,
                )
            if not legacy_path:
                raise
            self._legacy_routes = True
            Logger.warning(
                "event=relay_compatibility_used "
                f"compatibility={RELAY_COMPATIBILITY_VERSION} "
                "deprecated=legacy_route_and_header_mode "
                "replacement=/api/streamhouse/*+X-Streamhouse-* "
                f"remove_after={RELAY_COMPATIBILITY_REMOVE_AFTER}",
                source="TWITCH",
            )
            return self._request_once(
                legacy_path,
                method=method,
                payload=payload,
                include_legacy_headers=True,
            )
        except (URLError, OSError):
            if self._activate_legacy_host():
                return self._request(
                    path,
                    legacy_path=legacy_path,
                    method=method,
                    payload=payload,
                )
            raise

    def _activate_legacy_host(self) -> bool:
        if (
            self._active_base_url != STREAMHOUSE_RELAY_BASE_DEFAULT
            or self._legacy_host_fallback
        ):
            return False
        self._legacy_host_fallback = True
        self._active_base_url = LEGACY_RELAY_BASE_DEFAULT
        Logger.warning(
            "event=relay_compatibility_used "
            f"compatibility={RELAY_COMPATIBILITY_VERSION} "
            "deprecated=legacy_relay_hostname_fallback "
            f"replacement={STREAMHOUSE_RELAY_BASE_DEFAULT} "
            f"remove_after={RELAY_COMPATIBILITY_REMOVE_AFTER}",
            source="TWITCH",
        )
        return True

    def _request_once(
        self,
        path: str,
        *,
        method: str,
        payload: bytes | None,
        include_legacy_headers: bool = False,
    ) -> object:
        headers = {
            "Content-Type": "application/json",
            "X-Streamhouse-Channel": self.config.channel_id,
            "X-Streamhouse-Key": self.key,
        }
        if include_legacy_headers:
            headers.update(
                {
                    "X-Sally-Channel": self.config.channel_id,
                    "X-Sally-Key": self.key,
                }
            )
        request = Request(
            self._active_base_url + path,
            data=payload,
            method=method,
            headers=headers,
        )
        with urlopen(request, timeout=10) as response:
            body = response.read()
        return json.loads(body.decode("utf-8")) if body else {}

    def _put_config(self, payload: str) -> None:
        self._request(
            "/api/streamhouse/config",
            legacy_path="/api/sally/config",
            method="PUT",
            payload=payload.encode("utf-8"),
        )

    def _poll(self) -> list[object]:
        payload = self._request(
            "/api/streamhouse/poll",
            legacy_path="/api/sally/poll",
        )
        if not isinstance(payload, dict):
            raise ValueError("Relay returned an invalid response.")
        events = payload.get("events", [])
        if not isinstance(events, list):
            raise ValueError("Relay returned an invalid event list.")
        return events

    def _ack(self, event_id: str) -> None:
        self._request(
            "/api/streamhouse/ack",
            legacy_path="/api/sally/ack",
            method="POST",
            payload=json.dumps({"event_ids": [event_id]}).encode("utf-8"),
        )

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self.status_changed.emit(status)
