from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from PySide6.QtCore import QEventLoop, QObject, QSettings, QTimer, QUrl, Signal, Slot
from PySide6.QtWebSockets import QWebSocket

from products.hub.automation.cancellation import current_cancellation
from products.hub.automation.variable_registry import (
    VariableDataType,
    VariableDefinition,
    VariableSnapshot,
)
from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.qt_settings import (
    HUB_APPLICATION_NAME,
    streamhouse_qsettings,
)
from shared.streamhouse_runtime.version import VERSION


MUSIC_PROTOCOL_VERSION = 1
MUSIC_LOOPBACK_HOST = "127.0.0.1"
MUSIC_PLAYER_DIRECTORY_PARTS = ("YouTubeMusicDesktop", "YouTubeMusicDesktop")
MUSIC_CONNECTION_FILENAME = "connection.json"
MUSIC_TOKEN_FILENAME = "api_token"
MUSIC_COMMANDS = frozenset(
    {"play", "pause", "play_pause", "next", "previous", "set_volume", "set_muted"}
)
MUSIC_EVENT_TRACK_CHANGED = "track.changed"
MUSIC_EVENT_PLAYBACK_STARTED = "playback.started"
MUSIC_EVENT_PLAYBACK_PAUSED = "playback.paused"
MUSIC_EVENT_PLAYBACK_STOPPED = "playback.stopped"
MUSIC_EVENT_VOLUME_CHANGED = "volume.changed"
MUSIC_EVENT_PLAYER_CONNECTED = "player.connected"
MUSIC_EVENT_PLAYER_DISCONNECTED = "player.disconnected"
MUSIC_AUTOMATION_EVENT_TYPES = (
    MUSIC_EVENT_TRACK_CHANGED,
    MUSIC_EVENT_PLAYBACK_STARTED,
    MUSIC_EVENT_PLAYBACK_PAUSED,
    MUSIC_EVENT_PLAYBACK_STOPPED,
    MUSIC_EVENT_VOLUME_CHANGED,
    MUSIC_EVENT_PLAYER_CONNECTED,
    MUSIC_EVENT_PLAYER_DISCONNECTED,
)
MUSIC_EVENT_SNAPSHOT_MARKER = "__music_event_snapshot__"
RECONNECT_DELAYS_MS = (500, 1_000, 2_000, 5_000, 10_000)
MAX_MUSIC_MESSAGE_CHARS = 1_000_000


class MusicConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    NOT_RUNNING = "not_running"
    AUTHENTICATION_UNAVAILABLE = "authentication_unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"


@dataclass(frozen=True, slots=True)
class MusicPlayerConfig:
    auto_connect: bool = False

    def validate(self) -> None:
        if type(self.auto_connect) is not bool:
            raise ValueError("Music Player auto-connect must be a Boolean.")


class MusicPlayerConfigStore:
    """Machine-local preference storage; endpoint and token belong to the player."""

    AUTO_CONNECT_KEY = "integrations/music_player/auto_connect"

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
    ) -> None:
        self.settings = settings or streamhouse_qsettings(HUB_APPLICATION_NAME)

    def load(self) -> MusicPlayerConfig:
        return MusicPlayerConfig(
            auto_connect=self.settings.value(
                self.AUTO_CONNECT_KEY,
                False,
                type=bool,
            ),
        )

    def save(self, config: MusicPlayerConfig) -> None:
        config.validate()
        self.settings.setValue(self.AUTO_CONNECT_KEY, bool(config.auto_connect))
        self.settings.sync()


@dataclass(frozen=True, slots=True)
class MusicPlayerConnectionInfo:
    host: str
    port: int
    protocol_versions: tuple[int, ...]
    pid: int

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"


class MusicPlayerNotRunningError(FileNotFoundError):
    pass


class MusicPlayerAuthenticationUnavailableError(FileNotFoundError):
    pass


class MusicPlayerDiscoveryError(ValueError):
    pass


def default_music_player_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root.joinpath(*MUSIC_PLAYER_DIRECTORY_PARTS)


class MusicPlayerDiscovery:
    """Reads the standalone player's frozen, player-owned discovery contract."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_music_player_directory()
        self.connection_path = self.directory / MUSIC_CONNECTION_FILENAME
        self.token_path = self.directory / MUSIC_TOKEN_FILENAME

    def discover(self) -> tuple[MusicPlayerConnectionInfo, str]:
        try:
            raw = self.connection_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise MusicPlayerNotRunningError from error
        except OSError as error:
            raise MusicPlayerDiscoveryError("Connection information could not be read.") from error
        if len(raw) > 65_536:
            raise MusicPlayerDiscoveryError("Connection information is too large.")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise MusicPlayerDiscoveryError("Connection information is malformed.") from error
        if not isinstance(payload, dict):
            raise MusicPlayerDiscoveryError("Connection information must be an object.")

        versions = payload.get("protocol_versions")
        host = payload.get("host")
        port = payload.get("port")
        pid = payload.get("pid")
        if (
            not isinstance(versions, list)
            or not versions
            or any(type(version) is not int or version < 1 for version in versions)
        ):
            raise MusicPlayerDiscoveryError("Connection protocol versions are invalid.")
        if MUSIC_PROTOCOL_VERSION not in versions:
            raise MusicPlayerDiscoveryError("Connection protocol is not supported.")
        if host != MUSIC_LOOPBACK_HOST:
            raise MusicPlayerDiscoveryError("Connection host is not the required loopback address.")
        if type(port) is not int or not 1 <= port <= 65_535:
            raise MusicPlayerDiscoveryError("Connection port is invalid.")
        if type(pid) is not int or pid <= 0:
            raise MusicPlayerDiscoveryError("Connection process identifier is invalid.")

        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError) as error:
            raise MusicPlayerAuthenticationUnavailableError from error
        if len(token) < 32:
            raise MusicPlayerAuthenticationUnavailableError
        return (
            MusicPlayerConnectionInfo(
                host=host,
                port=port,
                protocol_versions=tuple(versions),
                pid=pid,
            ),
            token,
        )


@dataclass(frozen=True, slots=True)
class MusicPlayerState:
    sequence: int
    reason: str
    sent_at: str
    metadata_available: bool
    status: str
    position_ms: int | None
    position_sampled_at: str
    duration_ms: int | None
    title: str
    artist: str
    album: str
    media_id: str | None
    artwork_url: str
    volume: int | None
    muted: bool | None

    @classmethod
    def from_message(cls, message: Mapping[str, Any]) -> MusicPlayerState:
        if message.get("protocol_version") != MUSIC_PROTOCOL_VERSION:
            raise ValueError("State uses an unsupported protocol version.")
        sequence = message.get("sequence")
        if type(sequence) is not int or sequence < 0:
            raise ValueError("State sequence must be a non-negative integer.")
        playback = message.get("playback")
        track = message.get("track")
        audio = message.get("audio")
        if type(message.get("metadata_available")) is not bool:
            raise ValueError("State metadata_available must be a Boolean.")
        if not isinstance(playback, Mapping) or not isinstance(audio, Mapping):
            raise ValueError("State requires playback and audio objects.")
        if message["metadata_available"] and not isinstance(track, Mapping):
            raise ValueError("State with available metadata requires a track object.")
        if track is not None and not isinstance(track, Mapping):
            raise ValueError("State track must be an object or null.")
        track = track if isinstance(track, Mapping) else {}
        raw_muted = audio.get("muted")
        if raw_muted is not None and type(raw_muted) is not bool:
            raise ValueError("State muted must be a Boolean or null.")
        status = str(playback.get("status", "unknown")).strip().casefold()
        if status not in {"playing", "paused", "stopped", "unknown"}:
            status = "unknown"
        position_ms = _optional_bounded_integer(playback.get("position_ms"), minimum=0)
        duration_ms = _optional_bounded_integer(playback.get("duration_ms"), minimum=0)
        if duration_ms is not None and position_ms is not None:
            position_ms = min(position_ms, duration_ms)
        volume = _optional_bounded_integer(audio.get("volume"), minimum=0, maximum=100)
        artwork = track.get("artwork")
        artwork = artwork if isinstance(artwork, Mapping) else {}
        raw_media_id = track.get("media_id")
        return cls(
            sequence=sequence,
            reason=str(message.get("reason", "")).strip()[:128],
            sent_at=str(message.get("sent_at", "")).strip()[:128],
            metadata_available=message["metadata_available"],
            status=status,
            position_ms=position_ms,
            position_sampled_at=str(playback.get("position_sampled_at", "")).strip()[:128],
            duration_ms=duration_ms,
            title=_optional_text(track.get("title"), 1000),
            artist=_optional_text(track.get("artist"), 1000),
            album=_optional_text(track.get("album"), 1000),
            media_id=(str(raw_media_id).strip()[:512] if raw_media_id is not None else None),
            artwork_url=_optional_text(artwork.get("url"), 2048),
            volume=volume,
            muted=raw_muted,
        )

    def position_at(self, now: datetime | None = None) -> int | None:
        position = self.position_ms
        if position is None or self.status != "playing":
            return position
        sampled = _parse_utc(self.position_sampled_at)
        if sampled is not None:
            current = now or datetime.now(timezone.utc)
            elapsed_ms = max(0, round((current - sampled).total_seconds() * 1000))
            position += elapsed_ms
        return min(position, self.duration_ms) if self.duration_ms is not None else position


@dataclass(frozen=True, slots=True)
class MusicPlayerEvent:
    event_type: str
    context: Mapping[str, str]


@dataclass(slots=True)
class MusicCommandResult:
    succeeded: bool
    detail: str


@dataclass(slots=True)
class _PendingCommand:
    loop: QEventLoop
    result: MusicCommandResult | None = None


class MusicPlayerService(QObject):
    """Optional protocol-v1 client for one standalone loopback music player."""

    connection_state_changed = Signal(object, str)
    playback_state_changed = Signal(object)
    automation_event = Signal(object)

    def __init__(
        self,
        *,
        socket_factory: Callable[..., QWebSocket] = QWebSocket,
        discovery: MusicPlayerDiscovery | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._discovery = discovery or MusicPlayerDiscovery()
        self._socket = socket_factory(parent=self)
        self._socket.connected.connect(self._socket_connected)
        self._socket.disconnected.connect(self._socket_disconnected)
        self._socket.textMessageReceived.connect(self._handle_text_message)
        error_signal = getattr(self._socket, "errorOccurred", None)
        if error_signal is not None:
            error_signal.connect(self._socket_error)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._open_socket)
        self._handshake_timer = QTimer(self)
        self._handshake_timer.setSingleShot(True)
        self._handshake_timer.setInterval(5_000)
        self._handshake_timer.timeout.connect(self._handshake_timed_out)
        self._state = MusicConnectionState.DISCONNECTED
        self._detail = "Disconnected"
        self._token = ""
        self._connection_info: MusicPlayerConnectionInfo | None = None
        self._auto_connect = False
        self._want_connection = False
        self._started = False
        self._shutting_down = False
        self._hello_complete = False
        self._reconnect_attempt = 0
        self._capabilities: frozenset[str] = frozenset()
        self._playback: MusicPlayerState | None = None
        self._last_sequence = -1
        self._pending: dict[str, _PendingCommand] = {}
        self._automation_connected = False
        self._transition_baseline: MusicPlayerState | None = None
        self._track_identity: tuple[str, ...] | None = None

    @property
    def connection_state(self) -> MusicConnectionState:
        return self._state

    @property
    def status_detail(self) -> str:
        return self._detail

    @property
    def playback_state(self) -> MusicPlayerState | None:
        return self._playback

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def connected(self) -> bool:
        return self._state is MusicConnectionState.CONNECTED and self._hello_complete

    @property
    def wants_connection(self) -> bool:
        return self._want_connection

    @property
    def discovered_endpoint(self) -> str:
        return self._connection_info.endpoint if self._connection_info is not None else ""

    def configure(self, config: MusicPlayerConfig) -> None:
        config.validate()
        self._auto_connect = bool(config.auto_connect)

    def start(self) -> None:
        if self._shutting_down:
            return
        self._started = True
        if self._auto_connect:
            self.connect_to_player()

    def connect_to_player(self) -> bool:
        if self._shutting_down:
            return False
        if self._state in {
            MusicConnectionState.CONNECTING,
            MusicConnectionState.CONNECTED,
        }:
            return True
        self._want_connection = True
        self._reconnect_attempt = 0
        self._open_socket()
        return True

    def disconnect_from_player(self, *, reconnect: bool = False) -> None:
        self._want_connection = reconnect
        self._reconnect_timer.stop()
        self._handshake_timer.stop()
        self._emit_disconnected()
        self._hello_complete = False
        self._capabilities = frozenset()
        self._playback = None
        self._last_sequence = -1
        self._reset_transition_baseline()
        self._fail_pending("Music player disconnected.")
        self._socket.close()
        self._set_connection_state(MusicConnectionState.DISCONNECTED, "Disconnected")
        self.playback_state_changed.emit(None)

    @Slot()
    def _open_socket(self) -> None:
        if self._shutting_down or not self._want_connection:
            return
        try:
            connection_info, token = self._discovery.discover()
        except MusicPlayerNotRunningError:
            self._connection_info = None
            self._token = ""
            self._set_connection_state(
                MusicConnectionState.NOT_RUNNING,
                "Music Player is not running.",
            )
            self._schedule_reconnect()
            return
        except MusicPlayerAuthenticationUnavailableError:
            self._connection_info = None
            self._token = ""
            self._set_connection_state(
                MusicConnectionState.AUTHENTICATION_UNAVAILABLE,
                "Music Player authentication is unavailable.",
            )
            self._schedule_reconnect()
            return
        except MusicPlayerDiscoveryError:
            self._connection_info = None
            self._token = ""
            self._set_connection_state(
                MusicConnectionState.DISCONNECTED,
                "Music Player discovery is unavailable.",
            )
            self._schedule_reconnect()
            return
        self._connection_info = connection_info
        self._token = token
        self._hello_complete = False
        self._last_sequence = -1
        self._set_connection_state(MusicConnectionState.CONNECTING, "Connecting…")
        self._socket.open(QUrl(f"ws://{connection_info.endpoint}"))

    @Slot()
    def _socket_connected(self) -> None:
        if self._shutting_down or not self._want_connection:
            self._socket.close()
            return
        self._socket.sendTextMessage(
            json.dumps(
                {
                    "type": "hello",
                    "protocol_versions": [MUSIC_PROTOCOL_VERSION],
                    "client": {"name": "Streamhouse Hub", "version": VERSION},
                    "auth": {"token": self._token},
                },
                separators=(",", ":"),
            )
        )
        self._handshake_timer.start()

    @Slot(str)
    def _handle_text_message(self, raw_message: str) -> None:
        if self._shutting_down:
            return
        if len(raw_message) > MAX_MUSIC_MESSAGE_CHARS:
            Logger.warning("Music Player message exceeded the local size limit.", source="APP")
            return
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            Logger.warning("Music Player sent malformed JSON.", source="APP")
            return
        if not isinstance(message, dict):
            Logger.warning("Music Player sent a non-object message.", source="APP")
            return
        message_type = str(message.get("type", "")).strip().casefold()
        if message_type == "hello":
            self._accept_hello(message)
        elif message_type == "state":
            self._accept_state(message)
        elif message_type == "command_result":
            self._accept_command_result(message)
        elif message_type == "error":
            self._accept_protocol_error(message)
        else:
            Logger.warning("Music Player sent an unknown message type.", source="APP")

    def _accept_hello(self, message: Mapping[str, Any]) -> None:
        version = message.get("protocol_version")
        if version != MUSIC_PROTOCOL_VERSION:
            self._want_connection = False
            self._handshake_timer.stop()
            self._set_connection_state(
                MusicConnectionState.UNSUPPORTED_PROTOCOL,
                "Music Player protocol is not supported.",
            )
            self._socket.close()
            return
        capabilities = message.get("capabilities")
        if not isinstance(capabilities, Mapping):
            Logger.warning("Music Player hello omitted capabilities.", source="APP")
            self._socket.close()
            return
        commands = capabilities.get("commands", [])
        interval = capabilities.get("position_sync_interval_ms")
        if (
            not isinstance(commands, list)
            or capabilities.get("artwork_transport") != "url"
            or type(interval) is not int
            or interval <= 0
        ):
            self._want_connection = False
            self._handshake_timer.stop()
            self._set_connection_state(
                MusicConnectionState.UNSUPPORTED_PROTOCOL,
                "Music Player capabilities are not supported.",
            )
            self._socket.close()
            return
        self._capabilities = frozenset(
            str(command).strip().casefold()
            for command in commands
            if str(command).strip()
        )
        self._hello_complete = True
        self._handshake_timer.stop()
        self._reconnect_attempt = 0
        self._set_connection_state(MusicConnectionState.CONNECTED, "Connected")
        if not self._automation_connected:
            self._automation_connected = True
            self.automation_event.emit(
                MusicPlayerEvent(MUSIC_EVENT_PLAYER_CONNECTED, {})
            )

    def _accept_state(self, message: Mapping[str, Any]) -> None:
        if not self._hello_complete:
            Logger.warning("Music Player state arrived before hello completed.", source="APP")
            return
        try:
            state = MusicPlayerState.from_message(message)
        except (TypeError, ValueError) as error:
            Logger.warning(f"Music Player state was rejected: {error}", source="APP")
            return
        if state.sequence <= self._last_sequence:
            return
        self._last_sequence = state.sequence
        previous = self._transition_baseline
        self._playback = state
        if previous is None:
            self._transition_baseline = state
            self._track_identity = _stable_track_identity(state)
        else:
            self._emit_state_transitions(previous, state)
            self._transition_baseline = state
        self.playback_state_changed.emit(state)

    def _emit_state_transitions(
        self,
        previous: MusicPlayerState,
        current: MusicPlayerState,
    ) -> None:
        context = music_context_for_state(current)
        identity = _stable_track_identity(current)
        if identity is not None:
            if self._track_identity is not None and _track_identities_differ(
                self._track_identity, identity
            ):
                self.automation_event.emit(
                    MusicPlayerEvent(MUSIC_EVENT_TRACK_CHANGED, context)
                )
            self._track_identity = identity
        playback_events = {
            "playing": MUSIC_EVENT_PLAYBACK_STARTED,
            "paused": MUSIC_EVENT_PLAYBACK_PAUSED,
            "stopped": MUSIC_EVENT_PLAYBACK_STOPPED,
        }
        if current.status != previous.status and current.status in playback_events:
            self.automation_event.emit(
                MusicPlayerEvent(playback_events[current.status], context)
            )
        if (
            previous.volume is not None
            and current.volume is not None
            and previous.volume != current.volume
        ) or (
            previous.muted is not None
            and current.muted is not None
            and previous.muted != current.muted
        ):
            self.automation_event.emit(
                MusicPlayerEvent(MUSIC_EVENT_VOLUME_CHANGED, context)
            )

    def _accept_command_result(self, message: Mapping[str, Any]) -> None:
        if message.get("protocol_version") != MUSIC_PROTOCOL_VERSION:
            return
        request_id = str(message.get("request_id", "")).strip()
        pending = self._pending.get(request_id)
        if pending is None:
            return
        succeeded = message.get("ok") is True
        detail = "Music command completed." if succeeded else _safe_error_message(message)
        if self._token and self._token in detail:
            detail = detail.replace(self._token, "[REDACTED]")
        pending.result = MusicCommandResult(succeeded, detail)
        if pending.loop.isRunning():
            pending.loop.quit()

    def _accept_protocol_error(self, message: Mapping[str, Any]) -> None:
        code, _detail = _error_parts(message)
        if code in {
            "authentication_failed",
            "invalid_authentication",
            "invalid_token",
            "unauthorized",
        }:
            self._set_connection_state(
                MusicConnectionState.AUTHENTICATION_FAILED,
                "Music Player authentication failed.",
            )
            self._socket.close()
            return
        Logger.warning(f"Music Player protocol error ({code or 'unknown'}).", source="APP")

    @Slot()
    def _socket_disconnected(self) -> None:
        self._handshake_timer.stop()
        was_available = self._playback is not None
        self._emit_disconnected()
        self._hello_complete = False
        self._capabilities = frozenset()
        self._playback = None
        self._last_sequence = -1
        self._reset_transition_baseline()
        self._fail_pending("Music player disconnected before the command completed.")
        if self._state not in {
            MusicConnectionState.AUTHENTICATION_FAILED,
            MusicConnectionState.UNSUPPORTED_PROTOCOL,
        }:
            self._set_connection_state(MusicConnectionState.DISCONNECTED, "Disconnected")
        if was_available:
            self.playback_state_changed.emit(None)
        self._schedule_reconnect()

    def _emit_disconnected(self) -> None:
        if not self._automation_connected:
            return
        self._automation_connected = False
        if not self._shutting_down:
            context = (
                music_context_for_state(self._playback)
                if self._playback is not None
                else {}
            )
            self.automation_event.emit(
                MusicPlayerEvent(MUSIC_EVENT_PLAYER_DISCONNECTED, context)
            )

    def _reset_transition_baseline(self) -> None:
        self._transition_baseline = None
        self._track_identity = None

    @Slot()
    def _socket_error(self, *_args: object) -> None:
        if not self._shutting_down and self._state is MusicConnectionState.CONNECTING:
            self._set_connection_state(
                MusicConnectionState.DISCONNECTED,
                "Music Player is not available.",
            )

    @Slot()
    def _handshake_timed_out(self) -> None:
        if self._hello_complete or self._shutting_down:
            return
        self._socket.close()
        self._set_connection_state(
            MusicConnectionState.DISCONNECTED,
            "Music Player handshake timed out.",
        )

    def _schedule_reconnect(self) -> None:
        if self._shutting_down or not self._want_connection:
            return
        index = min(self._reconnect_attempt, len(RECONNECT_DELAYS_MS) - 1)
        self._reconnect_attempt += 1
        self._reconnect_timer.start(RECONNECT_DELAYS_MS[index])

    def send_command(
        self,
        command: str,
        arguments: Mapping[str, object] | None = None,
        *,
        timeout_ms: int = 5_000,
    ) -> MusicCommandResult:
        clean_command = str(command).strip().casefold()
        if clean_command not in MUSIC_COMMANDS:
            return MusicCommandResult(False, "Music command is not supported by Hub.")
        if not self.connected:
            return MusicCommandResult(False, "Music player is not connected.")
        if clean_command not in self._capabilities:
            return MusicCommandResult(
                False,
                f"Music player does not support {clean_command.replace('_', ' ')}.",
            )
        request_id = uuid4().hex
        loop = QEventLoop()
        pending = _PendingCommand(loop)
        self._pending[request_id] = pending
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        cancellation = current_cancellation()
        remove_cancellation_callback = (
            cancellation.add_callback(lambda _reason: loop.quit())
            if cancellation is not None
            else lambda: None
        )
        payload: dict[str, object] = {
            "type": "command",
            "protocol_version": MUSIC_PROTOCOL_VERSION,
            "request_id": request_id,
            "command": clean_command,
        }
        if arguments:
            payload["args"] = dict(arguments)
        try:
            self._socket.sendTextMessage(json.dumps(payload, separators=(",", ":")))
            if pending.result is None and not (cancellation and cancellation.cancelled):
                timer.start(max(100, min(int(timeout_ms), 60_000)))
                loop.exec()
            if pending.result is not None:
                return pending.result
            if cancellation is not None and cancellation.cancelled:
                return MusicCommandResult(False, "Music command was cancelled.")
            return MusicCommandResult(False, "Music player command timed out.")
        finally:
            timer.stop()
            remove_cancellation_callback()
            self._pending.pop(request_id, None)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._want_connection = False
        self._reconnect_timer.stop()
        self._handshake_timer.stop()
        self._fail_pending("Hub is shutting down.")
        try:
            self._socket.connected.disconnect(self._socket_connected)
            self._socket.disconnected.disconnect(self._socket_disconnected)
            self._socket.textMessageReceived.disconnect(self._handle_text_message)
        except (RuntimeError, TypeError):
            pass
        self._socket.close()
        self._playback = None
        self._capabilities = frozenset()
        self._hello_complete = False
        self._automation_connected = False
        self._reset_transition_baseline()
        self._token = ""
        self._connection_info = None
        self._set_connection_state(MusicConnectionState.DISCONNECTED, "Disconnected")

    def _fail_pending(self, detail: str) -> None:
        for pending in tuple(self._pending.values()):
            pending.result = MusicCommandResult(False, detail)
            if pending.loop.isRunning():
                pending.loop.quit()

    def _set_connection_state(self, state: MusicConnectionState, detail: str) -> None:
        changed = state is not self._state or detail != self._detail
        self._state = state
        self._detail = detail
        if changed:
            self.connection_state_changed.emit(state, detail)


MUSIC_VARIABLE_DEFINITIONS = (
    VariableDefinition("music.title", "Music Title", "Current track title.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.artist", "Music Artist", "Current track artist.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.album", "Music Album", "Current track album when supplied.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.artwork_url", "Music Artwork URL", "Current artwork URL supplied by the player.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.status", "Music Status", "Current playback status.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.duration", "Music Duration", "Current track duration in readable time.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.position", "Music Position", "Current playback position in readable time.", VariableDataType.TEXT, "Music Player", "Music"),
    VariableDefinition("music.volume", "Music Volume", "Current player volume from 0 to 100.", VariableDataType.INTEGER, "Music Player", "Music"),
    VariableDefinition("music.muted", "Music Muted", "Whether the player is muted.", VariableDataType.BOOLEAN, "Music Player", "Music"),
    VariableDefinition("music.media_id", "Music Media ID", "Current media identifier when supplied.", VariableDataType.TEXT, "Music Player", "Music"),
)


class MusicVariableProvider:
    source = "Music Player"
    _DEFINITIONS = MUSIC_VARIABLE_DEFINITIONS

    def __init__(self, service: MusicPlayerService) -> None:
        self._service = service

    def definitions(self) -> tuple[VariableDefinition, ...]:
        return self._DEFINITIONS

    def resolve(self, name: str, context: Mapping[str, object]) -> VariableSnapshot:
        definition = next(item for item in self._DEFINITIONS if item.name == name)
        if name in context:
            value: object = context[name]
            if definition.data_type is VariableDataType.INTEGER:
                value = int(str(value))
            elif definition.data_type is VariableDataType.BOOLEAN:
                value = str(value).strip().casefold() in {"true", "1", "yes", "on"}
            return VariableSnapshot(definition, value=value, available=True)
        if context.get(MUSIC_EVENT_SNAPSHOT_MARKER) == "true":
            return VariableSnapshot(
                definition,
                available=False,
                detail="The triggering player state did not supply this value.",
            )
        state = self._service.playback_state
        if not self._service.connected or state is None:
            return VariableSnapshot(
                definition,
                available=False,
                detail="Music player is not connected or has not supplied state.",
            )
        current_position = state.position_at()
        values: dict[str, object | None] = {
            "music.title": state.title or None,
            "music.artist": state.artist or None,
            "music.album": state.album or None,
            "music.artwork_url": state.artwork_url or None,
            "music.status": state.status,
            "music.duration": (
                _format_duration(state.duration_ms)
                if state.duration_ms is not None
                else None
            ),
            "music.position": (
                _format_duration(current_position) if current_position is not None else None
            ),
            "music.volume": state.volume,
            "music.muted": state.muted,
            "music.media_id": state.media_id,
        }
        value = values[name]
        optional = name in {
            "music.title",
            "music.artist",
            "music.album",
            "music.artwork_url",
            "music.duration",
            "music.position",
            "music.volume",
            "music.muted",
            "music.media_id",
        }
        return VariableSnapshot(
            definition,
            value=value,
            available=value is not None or not optional,
            detail="The player did not supply this value." if value is None else "",
        )

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        raise PermissionError(f'Variable "{name}" is read-only.')


def music_context_for_state(state: MusicPlayerState) -> dict[str, str]:
    position = state.position_at()
    values: dict[str, object | None] = {
        "music.title": state.title or None,
        "music.artist": state.artist or None,
        "music.album": state.album or None,
        "music.artwork_url": state.artwork_url or None,
        "music.status": state.status,
        "music.duration": _format_duration(state.duration_ms) if state.duration_ms is not None else None,
        "music.position": _format_duration(position) if position is not None else None,
        "music.volume": state.volume,
        "music.muted": "true" if state.muted is True else "false" if state.muted is False else None,
        "music.media_id": state.media_id,
    }
    return {
        MUSIC_EVENT_SNAPSHOT_MARKER: "true",
        **{key: str(value) for key, value in values.items() if value is not None},
    }


def _stable_track_identity(state: MusicPlayerState) -> tuple[str, ...] | None:
    media_id = state.media_id.strip().casefold() if state.media_id else ""
    title = " ".join(state.title.split()).casefold()
    artist = " ".join(state.artist.split()).casefold()
    album = " ".join(state.album.split()).casefold()
    if not media_id and (not title or not artist):
        return None
    return (media_id, title, artist, album)


def _track_identities_differ(
    previous: tuple[str, ...], current: tuple[str, ...]
) -> bool:
    previous_media, *previous_metadata = previous
    current_media, *current_metadata = current
    if previous_media and current_media:
        return previous_media != current_media
    if all(previous_metadata[:2]) and all(current_metadata[:2]):
        return previous_metadata != current_metadata
    return previous != current


def _bounded_integer(value: object, *, minimum: int, maximum: int = 2**63 - 1) -> int:
    if type(value) not in {int, float}:
        raise ValueError("State numeric values must be numbers.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("State numeric values must be finite.")
    return max(minimum, min(round(numeric), maximum))


def _optional_bounded_integer(
    value: object,
    *,
    minimum: int,
    maximum: int = 2**63 - 1,
) -> int | None:
    return None if value is None else _bounded_integer(value, minimum=minimum, maximum=maximum)


def _optional_text(value: object, limit: int) -> str:
    return "" if value is None else str(value).strip()[:limit]


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_duration(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _error_parts(message: Mapping[str, Any]) -> tuple[str, str]:
    raw_error = message.get("error")
    if isinstance(raw_error, Mapping):
        code = str(raw_error.get("code", "request_failed")).strip().casefold()
        detail = str(raw_error.get("message", "Music player request failed.")).strip()
    else:
        code = str(message.get("code", "request_failed")).strip().casefold()
        detail = str(message.get("message", "Music player request failed.")).strip()
    return code[:128], (detail or "Music player request failed.")[:500]


def _safe_error_message(message: Mapping[str, Any]) -> str:
    _code, detail = _error_parts(message)
    return detail
