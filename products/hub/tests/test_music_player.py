from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.music_tasks import MusicCommandTask
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.integrations.music_player import (
    MUSIC_LOOPBACK_HOST,
    MUSIC_PROTOCOL_VERSION,
    MUSIC_VARIABLE_DEFINITIONS,
    MusicConnectionState,
    MusicPlayerConfig,
    MusicPlayerConfigStore,
    MusicPlayerConnectionInfo,
    MusicPlayerDiscovery,
    MusicPlayerDiscoveryError,
    MusicPlayerService,
    MusicVariableProvider,
)


class _FakeSocket(QObject):
    connected = Signal()
    disconnected = Signal()
    textMessageReceived = Signal(str)
    errorOccurred = Signal(object)

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.urls: list[str] = []
        self.sent: list[dict[str, object]] = []
        self.closed = 0
        self.on_send = None

    def open(self, url) -> None:
        self.urls.append(url.toString())

    def sendTextMessage(self, message: str) -> int:
        payload = json.loads(message)
        self.sent.append(payload)
        if self.on_send is not None:
            self.on_send(payload)
        return len(message)

    def close(self) -> None:
        self.closed += 1


class _FakeDiscovery:
    def __init__(self, port: int = 9123, token: str = "local-secret-that-is-long-enough-123") -> None:
        self.port = port
        self.token = token

    def discover(self) -> tuple[MusicPlayerConnectionInfo, str]:
        return (
            MusicPlayerConnectionInfo(MUSIC_LOOPBACK_HOST, self.port, (1,), 1234),
            self.token,
        )


def _service() -> tuple[MusicPlayerService, _FakeSocket]:
    QApplication.instance() or QApplication([])
    socket = _FakeSocket()
    service = MusicPlayerService(
        socket_factory=lambda **_kwargs: socket,
        discovery=_FakeDiscovery(),
    )
    service.configure(MusicPlayerConfig())
    return service, socket


def _hello(socket: _FakeSocket, commands: list[str] | None = None) -> None:
    socket.textMessageReceived.emit(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": MUSIC_PROTOCOL_VERSION,
                "server": {"name": "Test Player"},
                "capabilities": {
                    "commands": commands or [],
                    "artwork_transport": "url",
                    "position_sync_interval_ms": 10_000,
                },
            }
        )
    )


def _state(sequence: int, **updates: object) -> dict[str, object]:
    sampled = datetime.now(timezone.utc).isoformat()
    message: dict[str, object] = {
        "type": "state",
        "protocol_version": MUSIC_PROTOCOL_VERSION,
        "sequence": sequence,
        "reason": "track_changed",
        "sent_at": sampled,
        "metadata_available": True,
        "playback": {
            "status": "playing",
            "position_ms": 53_210,
            "position_sampled_at": sampled,
            "duration_ms": 243_000,
        },
        "track": {
            "title": "A Song",
            "artist": "An Artist",
            "album": "An Album",
            "media_id": "media-1",
            "artwork": {"url": "https://images.example/art.jpg"},
        },
        "audio": {"volume": 72, "muted": False},
    }
    message.update(updates)
    return message


def _connect(service: MusicPlayerService, socket: _FakeSocket, commands=None) -> None:
    assert service.connect_to_player()
    assert socket.urls == [f"ws://{MUSIC_LOOPBACK_HOST}:9123"]
    socket.connected.emit()
    hello = socket.sent[-1]
    assert hello["type"] == "hello"
    assert hello["protocol_versions"] == [MUSIC_PROTOCOL_VERSION]
    assert hello["client"] == {"name": "Streamhouse Hub", "version": "0.1.0"}
    assert hello["auth"] == {"token": "local-secret-that-is-long-enough-123"}
    _hello(socket, commands)
    assert service.connected


def test_loopback_handshake_protocol_and_unsupported_version() -> None:
    service, socket = _service()
    _connect(service, socket, ["play"])
    assert service.capabilities == frozenset({"play"})

    service.disconnect_from_player()
    assert service.connection_state is MusicConnectionState.DISCONNECTED
    service.connect_to_player()
    socket.connected.emit()
    socket.textMessageReceived.emit(
        json.dumps({"type": "hello", "protocol_version": 99, "capabilities": {}})
    )
    assert service.connection_state is MusicConnectionState.UNSUPPORTED_PROTOCOL
    assert not service.connected
    service.shutdown()


def test_authentication_failure_is_reported_and_reconnects_with_fresh_discovery() -> None:
    service, socket = _service()
    assert service.connect_to_player()
    socket.connected.emit()
    socket.textMessageReceived.emit(
        json.dumps(
            {
                "type": "error",
                "protocol_version": 1,
                "error": {
                    "code": "invalid_authentication",
                    "message": "secret detail",
                },
            }
        )
    )
    assert service.connection_state is MusicConnectionState.AUTHENTICATION_FAILED
    assert service.status_detail == "Music Player authentication failed."
    socket.disconnected.emit()
    assert service._reconnect_timer.isActive()
    assert service._reconnect_timer.interval() == 500
    service.shutdown()


def test_incompatible_capabilities_are_rejected() -> None:
    service, socket = _service()
    service.connect_to_player()
    socket.connected.emit()
    socket.textMessageReceived.emit(
        json.dumps(
            {
                "type": "hello",
                "protocol_version": 1,
                "capabilities": {
                    "commands": ["play"],
                    "artwork_transport": "base64",
                    "position_sync_interval_ms": 10_000,
                },
            }
        )
    )
    assert service.connection_state is MusicConnectionState.UNSUPPORTED_PROTOCOL
    assert not service.connected
    service.shutdown()


def test_machine_local_config_contains_only_auto_connect_preference() -> None:
    with tempfile.TemporaryDirectory() as directory:
        settings_path = Path(directory) / "settings.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        store = MusicPlayerConfigStore(settings=settings)
        store.save(MusicPlayerConfig(auto_connect=True))

        config = store.load()
        assert config == MusicPlayerConfig(auto_connect=True)
        settings_text = settings_path.read_text(encoding="utf-8")
        assert "port" not in settings_text.casefold()
        assert "token" not in settings_text.casefold()


def _write_player_contract(
    directory: Path,
    *,
    port: int = 17823,
    token: str = "player-owned-token-that-is-long-enough-123",
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "connection.json").write_text(
        json.dumps(
            {
                "protocol_versions": [1],
                "host": MUSIC_LOOPBACK_HOST,
                "port": port,
                "token": "connection-file-token-is-not-the-authority",
                "pid": 12345,
            }
        ),
        encoding="utf-8",
    )
    (directory / "api_token").write_text(token + "\n", encoding="utf-8")


def test_discovery_reads_dynamic_endpoint_and_player_owned_token(tmp_path: Path) -> None:
    _write_player_contract(tmp_path, port=49_152)
    info, token = MusicPlayerDiscovery(tmp_path).discover()
    assert info.endpoint == "127.0.0.1:49152"
    assert info.protocol_versions == (1,)
    assert token == "player-owned-token-that-is-long-enough-123"
    assert "connection-file-token" not in token


def test_discovery_rejects_malformed_or_non_loopback_contract(tmp_path: Path) -> None:
    (tmp_path / "connection.json").write_text("not json", encoding="utf-8")
    with pytest.raises(MusicPlayerDiscoveryError):
        MusicPlayerDiscovery(tmp_path).discover()
    _write_player_contract(tmp_path)
    payload = json.loads((tmp_path / "connection.json").read_text(encoding="utf-8"))
    payload["host"] = "0.0.0.0"
    (tmp_path / "connection.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MusicPlayerDiscoveryError):
        MusicPlayerDiscovery(tmp_path).discover()


def test_absent_connection_and_missing_token_are_quiet_states(tmp_path: Path) -> None:
    socket = _FakeSocket()
    service = MusicPlayerService(
        socket_factory=lambda **_kwargs: socket,
        discovery=MusicPlayerDiscovery(tmp_path),
    )
    assert service.connect_to_player()
    assert service.connection_state is MusicConnectionState.NOT_RUNNING
    assert socket.urls == []
    assert service._reconnect_timer.isActive()

    _write_player_contract(tmp_path)
    (tmp_path / "api_token").unlink()
    service._reconnect_timer.stop()
    service._open_socket()
    assert service.connection_state is MusicConnectionState.AUTHENTICATION_UNAVAILABLE
    assert socket.urls == []
    service.shutdown()


def test_reconnect_rediscovers_fallback_port_instead_of_reusing_stale_port(
    tmp_path: Path,
) -> None:
    _write_player_contract(tmp_path, port=17823)
    socket = _FakeSocket()
    service = MusicPlayerService(
        socket_factory=lambda **_kwargs: socket,
        discovery=MusicPlayerDiscovery(tmp_path),
    )
    service.connect_to_player()
    assert socket.urls == ["ws://127.0.0.1:17823"]

    _write_player_contract(tmp_path, port=54_321)
    socket.disconnected.emit()
    service._reconnect_timer.stop()
    service._open_socket()
    assert socket.urls[-1] == "ws://127.0.0.1:54321"
    assert service.discovered_endpoint == "127.0.0.1:54321"
    service.shutdown()


def test_full_state_replaces_current_and_stale_sequence_is_ignored() -> None:
    service, socket = _service()
    _connect(service, socket)
    socket.textMessageReceived.emit(json.dumps(_state(42)))
    first = service.playback_state
    assert first is not None and first.title == "A Song"

    stale = _state(41)
    stale["track"] = {"title": "Stale", "artist": "", "album": "", "artwork": {}}
    socket.textMessageReceived.emit(json.dumps(stale))
    assert service.playback_state is first

    replacement = _state(43)
    replacement["track"] = {
        "title": "New Song",
        "artist": "New Artist",
        "album": None,
        "media_id": None,
        "artwork": {},
    }
    socket.textMessageReceived.emit(json.dumps(replacement))
    assert service.playback_state is not first
    assert service.playback_state.title == "New Song"
    assert service.playback_state.album == ""
    assert service.playback_state.media_id is None
    service.shutdown()


def test_frozen_protocol_initial_state_accepts_null_track_and_unknown_values() -> None:
    service, socket = _service()
    provider = MusicVariableProvider(service)
    registry = VariableRegistry()
    registry.register(provider)
    _connect(service, socket)
    sampled = datetime.now(timezone.utc).isoformat()
    socket.textMessageReceived.emit(
        json.dumps(
            {
                "type": "state",
                "protocol_version": 1,
                "sequence": 1,
                "reason": "initial",
                "sent_at": sampled,
                "metadata_available": False,
                "playback": {
                    "status": "unknown",
                    "position_ms": None,
                    "position_sampled_at": sampled,
                    "duration_ms": None,
                },
                "track": None,
                "audio": {"volume": None, "muted": None},
            }
        )
    )
    assert service.playback_state is not None
    assert service.playback_state.status == "unknown"
    assert registry.resolve("music.status").display_value == "unknown"
    assert not registry.resolve("music.title").available
    assert not registry.resolve("music.position").available
    assert not registry.resolve("music.volume").available
    assert not registry.resolve("music.muted").available
    service.shutdown()


def test_malformed_messages_do_not_replace_valid_state() -> None:
    service, socket = _service()
    _connect(service, socket)
    socket.textMessageReceived.emit(json.dumps(_state(1)))
    valid = service.playback_state
    socket.textMessageReceived.emit("not json")
    socket.textMessageReceived.emit(json.dumps(["not", "an", "object"]))
    socket.textMessageReceived.emit(json.dumps({"type": "state", "sequence": 2}))
    assert service.playback_state is valid
    service.shutdown()


def test_variables_are_discoverable_and_become_unavailable_on_disconnect() -> None:
    service, socket = _service()
    provider = MusicVariableProvider(service)
    registry = VariableRegistry()
    registry.register(provider)
    assert {item.name for item in provider.definitions()} == {
        item.name for item in MUSIC_VARIABLE_DEFINITIONS
    }
    assert registry.resolve("music.title").available is False

    _connect(service, socket)
    message = _state(1)
    message["playback"] = {
        "status": "paused",
        "position_ms": 61_000,
        "position_sampled_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 3_661_000,
    }
    socket.textMessageReceived.emit(json.dumps(message))
    assert registry.resolve("music.title").display_value == "A Song"
    assert registry.resolve("music.artist").display_value == "An Artist"
    assert registry.resolve("music.album").display_value == "An Album"
    assert registry.resolve("music.artwork_url").display_value.endswith("art.jpg")
    assert registry.resolve("music.status").display_value == "paused"
    assert registry.resolve("music.duration").display_value == "1:01:01"
    assert registry.resolve("music.position").display_value == "1:01"
    assert registry.resolve("music.volume").value == 72
    assert registry.resolve("music.muted").value is False
    assert registry.resolve("music.media_id").value == "media-1"

    service.disconnect_from_player()
    assert registry.resolve("music.title").available is False
    service.shutdown()


def test_playing_position_is_inferred_without_mutating_snapshot() -> None:
    state = _state(1)
    sampled = datetime.now(timezone.utc) - timedelta(seconds=5)
    state["playback"] = {
        "status": "playing",
        "position_ms": 10_000,
        "position_sampled_at": sampled.isoformat(),
        "duration_ms": 20_000,
    }
    from products.hub.integrations.music_player import MusicPlayerState

    parsed = MusicPlayerState.from_message(state)
    assert 14_900 <= parsed.position_at(datetime.now(timezone.utc)) <= 15_100
    assert parsed.position_ms == 10_000
    assert parsed.position_at(sampled + timedelta(seconds=30)) == 20_000


def test_command_result_is_correlated_and_state_is_not_optimistically_changed() -> None:
    service, socket = _service()
    _connect(service, socket, ["next", "set_volume"])
    socket.textMessageReceived.emit(json.dumps(_state(1)))
    original = service.playback_state

    request_ids: list[str] = []

    def reply(payload: dict[str, object]) -> None:
        if payload.get("type") != "command":
            return
        request_ids.append(str(payload["request_id"]))
        socket.textMessageReceived.emit(
            json.dumps(
                {
                    "type": "command_result",
                    "protocol_version": 1,
                    "request_id": payload["request_id"],
                    "ok": True,
                }
            )
        )

    socket.on_send = reply
    first = service.send_command("next")
    second = service.send_command("set_volume", {"volume": 55})
    assert first.succeeded and second.succeeded
    assert len(request_ids) == len(set(request_ids)) == 2
    assert socket.sent[-1]["args"] == {"volume": 55}
    assert service.playback_state is original
    assert not service.send_command("previous").succeeded
    service.shutdown()


def test_command_failure_timeout_and_disconnect_are_clean() -> None:
    service, socket = _service()
    _connect(service, socket, ["play", "pause", "next"])

    def reject(payload: dict[str, object]) -> None:
        if payload.get("type") == "command":
            socket.textMessageReceived.emit(
                json.dumps(
                    {
                        "type": "command_result",
                        "protocol_version": 1,
                        "request_id": payload["request_id"],
                        "ok": False,
                        "error": {"code": "rejected", "message": "Player rejected it."},
                    }
                )
            )

    socket.on_send = reject
    rejected = service.send_command("play")
    assert not rejected.succeeded
    assert rejected.detail == "Player rejected it."

    socket.on_send = None
    timed_out = service.send_command("pause", timeout_ms=1)
    assert not timed_out.succeeded
    assert "timed out" in timed_out.detail

    socket.on_send = lambda payload: (
        socket.disconnected.emit() if payload.get("type") == "command" else None
    )
    disconnected = service.send_command("next")
    assert not disconnected.succeeded
    assert "disconnected" in disconnected.detail
    assert service._reconnect_timer.isActive()
    assert service._reconnect_timer.interval() == 500
    service.shutdown()


def test_new_connection_resets_sequence_tracking_and_refreshes_state() -> None:
    service, socket = _service()
    _connect(service, socket)
    socket.textMessageReceived.emit(json.dumps(_state(50)))
    assert service.playback_state.sequence == 50

    socket.disconnected.emit()
    service._reconnect_timer.stop()
    service._open_socket()
    socket.connected.emit()
    _hello(socket)
    socket.textMessageReceived.emit(json.dumps(_state(1)))
    assert service.playback_state.sequence == 1
    service.shutdown()


class _TaskService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def send_command(self, command, arguments, *, timeout_ms):
        from products.hub.integrations.music_player import MusicCommandResult

        self.calls.append((command, dict(arguments)))
        return MusicCommandResult(True, "Accepted")


def test_music_tasks_resolve_volume_and_send_protocol_booleans() -> None:
    registry = VariableRegistry()
    service = _TaskService()
    trigger = TriggerEvent("test", "core", "manual", {"custom.level": "45"})
    volume = MusicCommandTask("music.set_volume", "set_volume", service, registry)
    muted = MusicCommandTask("music.set_muted", "set_muted", service, registry)

    result = volume.execute(
        TaskDefinition("1", "music.set_volume", "Volume", {"volume": "{custom.level}"}),
        trigger,
    )
    assert result.succeeded
    assert service.calls[-1] == ("set_volume", {"volume": 45})
    result = muted.execute(
        TaskDefinition("2", "music.set_muted", "Muted", {"muted": False}),
        trigger,
    )
    assert result.succeeded
    assert service.calls[-1] == ("set_muted", {"muted": False})

    invalid = volume.execute(
        TaskDefinition("3", "music.set_volume", "Bad", {"volume": "101"}),
        trigger,
    )
    assert not invalid.succeeded
    assert "0 to 100" in invalid.detail
    invalid_muted = muted.execute(
        TaskDefinition("4", "music.set_muted", "Bad", {"muted": "false"}),
        trigger,
    )
    assert not invalid_muted.succeeded


def test_shutdown_stops_reconnect_and_rejects_late_commands() -> None:
    service, socket = _service()
    service.start()
    _connect(service, socket, ["play"])
    service.shutdown()
    assert service.connection_state is MusicConnectionState.DISCONNECTED
    assert not service.send_command("play").succeeded
    socket.textMessageReceived.emit(json.dumps(_state(99)))
    assert service.playback_state is None
