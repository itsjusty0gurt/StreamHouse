from __future__ import annotations

import faulthandler
import json
import os
import platform
import re
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from shared.streamhouse_runtime.json_store import atomic_write_json
from shared.streamhouse_runtime.logger import Logger, format_traceback_locations
from shared.streamhouse_runtime.paths import user_data_root
from shared.streamhouse_runtime.redaction import (
    is_secret_key,
    redact_secret_text,
)
from shared.streamhouse_runtime.version import VERSION

def sanitize_support_text(value: object, *, home: Path | None = None) -> str:
    """Redact likely credentials and local user-home paths from a copied artifact."""
    text = redact_secret_text(value)
    home_text = str(home or Path.home())
    if home_text:
        text = re.sub(re.escape(home_text), "<USER_HOME>", text, flags=re.IGNORECASE)
    return text


def sanitize_support_data(value: Any) -> Any:
    """Recursively sanitize structured data without breaking its JSON shape."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<REDACTED>"
                if is_secret_key(key)
                else "<PRIVATE CONTENT OMITTED>"
                if str(key).strip().casefold().replace("-", "_")
                in {
                    "chat_message",
                    "context",
                    "context_values",
                    "message",
                    "message_content",
                    "message_text",
                    "payload",
                    "raw_event",
                    "raw_payload",
                }
                else sanitize_support_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_support_data(item) for item in value]
    if isinstance(value, str):
        return sanitize_support_text(value)
    return value


def redact_sensitive_text(value: object) -> str:
    """Redact application UI/history text using its established marker."""
    return sanitize_support_text(value).replace("<REDACTED>", "[REDACTED]")


class DiagnosticsService:
    """Own Hub session, crash, and shareable support diagnostics."""

    MARKER_VERSION = 1
    NORMAL_LOG_RETENTION = 10
    CRASH_RETENTION = 5

    def __init__(
        self,
        root: Path | None = None,
        *,
        process_checker: Callable[[int], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root or user_data_root()
        self.logs_directory = self.root / "logs"
        self.crashes_directory = self.root / "crashes"
        self.support_directory = self.root / "support"
        self.marker_path = self.root / "diagnostics" / "active-session.json"
        for directory in (
            self.logs_directory,
            self.crashes_directory,
            self.support_directory,
            self.marker_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._process_checker = process_checker or self._process_is_running
        self.session_id = uuid4().hex
        self.started_at = self._now()
        self.previous_session: dict[str, Any] | None = None
        self.previous_shutdown = "Unknown"
        self._state_provider: Callable[[], Mapping[str, Any]] = lambda: {}
        self._original_sys_hook = None
        self._original_thread_hook = None
        self._original_unraisable_hook = None
        self._original_qt_handler = None
        self._qt_handler_installed = False
        self._fault_write_lock = threading.RLock()
        self._fault_file = None
        self._fault_path: Path | None = None
        self.previous_fault_path: Path | None = None
        self._load_previous_marker()
        self._finalize_previous_abnormal_artifact()
        self._rotate(
            self.crashes_directory,
            "StreamhouseHub-Crash-*.log",
            self.CRASH_RETENTION,
        )
        self._rotate(
            self.crashes_directory,
            "StreamhouseHub-Fault-*.log",
            self.CRASH_RETENTION,
        )
        # Fault capture is deliberately established before the active marker and
        # normal logger. A hard process termination cannot run a handler, so the
        # already-flushed artifact is the minimum durable evidence for the next
        # launch.
        self._start_fault_capture()
        self._write_active_marker()

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ValueError):
            return False
        return True

    def _load_previous_marker(self) -> None:
        if not self.marker_path.exists():
            self.previous_shutdown = "Clean"
            return
        try:
            value = json.loads(self.marker_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != self.MARKER_VERSION:
                raise ValueError("unsupported marker")
            self.previous_session = value
            previous_pid = int(value.get("pid", 0))
            self.previous_shutdown = (
                "Active" if self._process_checker(previous_pid) else "Abnormal"
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.previous_shutdown = "Unknown"

    def _write_active_marker(self) -> None:
        atomic_write_json(
            self.marker_path,
            {
                "version": self.MARKER_VERSION,
                "session_id": self.session_id,
                "pid": os.getpid(),
                "started_at": self.started_at.isoformat(),
                "fault_artifact": (
                    self._fault_path.name if self._fault_path is not None else None
                ),
            },
        )

    def _start_fault_capture(self) -> None:
        fault_path = self.crashes_directory / (
            f"StreamhouseHub-Fault-{self._stamp()}-{self.session_id[:8]}.log"
        )
        self._fault_path = fault_path
        try:
            self._fault_file = fault_path.open("a", encoding="utf-8")
            self._write_fault_text(
                "Streamhouse Hub Session Fault Record\n"
                f"Session: {self.session_id}\n"
                f"Started: {self.started_at.isoformat()}\n"
                f"Process ID: {os.getpid()}\n"
                f"Hub Version: {VERSION}\n"
                "Initial Capture Status: No in-process exception captured yet.\n"
                f"Checkpoint: diagnostics initialized at {self._now().isoformat()}\n",
                durable=True,
            )
            faulthandler.enable(file=self._fault_file, all_threads=True)
        except (OSError, RuntimeError) as error:
            # Logger may not exist yet; startup continues so the normal session
            # logger can report the reduced diagnostics coverage once available.
            self._close_fault_file(disable=True)
            self._fault_path = None
            self._fault_start_error = type(error).__name__
        else:
            self._fault_start_error = None

    def checkpoint(self, label: str) -> None:
        """Persist a low-volume, content-free lifecycle checkpoint."""
        safe_label = re.sub(r"[^A-Za-z0-9 ._:/()-]", "?", str(label))[:160]
        self._write_fault_text(
            f"Checkpoint: {safe_label} at {self._now().isoformat()}\n",
            durable=True,
        )

    def _write_fault_text(self, text: str, *, durable: bool = False) -> None:
        if self._fault_file is None:
            return
        with self._fault_write_lock:
            try:
                self._fault_file.write(sanitize_support_text(text))
                self._fault_file.flush()
                if durable:
                    os.fsync(self._fault_file.fileno())
            except (OSError, ValueError):
                pass

    def _finalize_previous_abnormal_artifact(self) -> None:
        if not self.previous_shutdown_abnormal or self.previous_session is None:
            return
        previous_id = str(self.previous_session.get("session_id", "unknown"))
        artifact_name = self.previous_session.get("fault_artifact")
        artifact = None
        if (
            isinstance(artifact_name, str)
            and artifact_name
            and Path(artifact_name).name == artifact_name
        ):
            candidate = self.crashes_directory / artifact_name
            if candidate.is_file():
                artifact = candidate
        if artifact is None:
            artifact = self.crashes_directory / (
                f"StreamhouseHub-Fault-{self._stamp()}-{previous_id[:8]}.log"
            )
            try:
                artifact.write_text(
                    sanitize_support_text(
                        "Streamhouse Hub Session Fault Record\n"
                        f"Session: {previous_id}\n"
                        f"Started: "
                        f"{self.previous_session.get('started_at', 'Unknown')}\n"
                        f"Process ID: {self.previous_session.get('pid', 'Unknown')}\n"
                        "Capture Status: Fault capture artifact was unavailable "
                        "from the prior runtime.\n"
                    ),
                    encoding="utf-8",
                )
            except OSError:
                return
        try:
            existing = artifact.read_text(encoding="utf-8", errors="replace")
            if "Abnormal termination detected by session:" not in existing:
                captured = self._artifact_has_captured_fault(existing)
                classification = (
                    "Abnormal termination detected; in-process exception or fault "
                    "output was captured."
                    if captured
                    else "Abnormal termination detected; no in-process exception "
                    "was captured."
                )
                with artifact.open("a", encoding="utf-8") as stream:
                    stream.write(
                        "\nPrevious-session classification\n"
                        f"Detected: {self._now().isoformat()}\n"
                        f"Abnormal termination detected by session: {self.session_id}\n"
                        f"Status: {classification}\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError:
            return
        self.previous_fault_path = artifact

    @staticmethod
    def _artifact_has_captured_fault(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                "Captured in-process exception:",
                "Fatal Python error:",
                "Windows fatal exception:",
            )
        )

    @property
    def previous_shutdown_abnormal(self) -> bool:
        return self.previous_shutdown == "Abnormal"

    def set_state_provider(self, provider: Callable[[], Mapping[str, Any]]) -> None:
        self._state_provider = provider

    def install_exception_hooks(self) -> None:
        if self._original_sys_hook is not None:
            return
        self._original_sys_hook = sys.excepthook
        self._original_thread_hook = threading.excepthook
        self._original_unraisable_hook = sys.unraisablehook
        sys.excepthook = self._handle_sys_exception
        threading.excepthook = self._handle_thread_exception
        sys.unraisablehook = self._handle_unraisable
        if self._fault_start_error is not None:
            Logger.warning(
                "Python faulthandler could not be enabled: "
                f"{self._fault_start_error}",
                source="SYSTEM",
            )
        self.checkpoint("Python exception hooks installed")

    def install_qt_message_handler(self) -> None:
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        except ImportError:
            return

        levels = {
            QtMsgType.QtInfoMsg: Logger.info,
            QtMsgType.QtWarningMsg: Logger.warning,
            QtMsgType.QtCriticalMsg: Logger.error,
            QtMsgType.QtFatalMsg: Logger.critical,
        }

        def handler(message_type, _context, message) -> None:
            callback = levels.get(message_type)
            if callback is not None:
                callback(sanitize_support_text(message), source="QT")

        self._original_qt_handler = qInstallMessageHandler(handler)
        self._qt_handler_installed = True

    def _handle_sys_exception(self, exception_type, exception_value, exception_traceback) -> None:
        if issubclass(exception_type, KeyboardInterrupt):
            self._original_sys_hook(exception_type, exception_value, exception_traceback)
            return
        self.record_exception(
            "Unhandled application exception",
            exception_type,
            exception_value,
            exception_traceback,
        )
        self._original_sys_hook(exception_type, exception_value, exception_traceback)

    def _handle_thread_exception(self, args) -> None:
        self.record_exception(
            f'Unhandled worker-thread exception ({getattr(args.thread, "name", "unknown")})',
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )
        if self._original_thread_hook is not None:
            self._original_thread_hook(args)

    def _handle_unraisable(self, args) -> None:
        self.record_exception(
            "Unraisable Python exception",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )
        if self._original_unraisable_hook is not None:
            self._original_unraisable_hook(args)

    def record_exception(
        self,
        label: str,
        exception_type,
        exception_value,
        exception_traceback,
    ) -> Path:
        rendered = (
            format_traceback_locations(exception_traceback)
            + f"{exception_type.__name__}: <exception message omitted for privacy>\n"
        )
        safe_traceback = sanitize_support_text(rendered)
        Logger.critical(f"{label}:\n{safe_traceback}", source="SYSTEM")
        self._write_fault_text(
            f"Captured in-process exception: {label} at {self._now().isoformat()}\n",
            durable=True,
        )
        report = self.crashes_directory / (
            f"StreamhouseHub-Crash-{self._stamp()}-{self.session_id[:8]}.log"
        )
        try:
            with report.open("w", encoding="utf-8") as stream:
                stream.write(self._crash_header(label) + "\n" + safe_traceback)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            Logger.error(
                f"Could not write the dedicated crash report: {type(error).__name__}",
                source="SYSTEM",
            )
        Logger.flush()
        self._rotate(
            self.crashes_directory,
            "StreamhouseHub-Crash-*.log",
            self.CRASH_RETENTION,
        )
        return report

    def _crash_header(self, label: str) -> str:
        log_path = Logger.session_log_path()
        return (
            "Streamhouse Hub Crash Report\n"
            f"Session: {self.session_id}\n"
            f"Timestamp: {self._now().isoformat()}\n"
            f"Hub Version: {VERSION}\n"
            f"Type: {label}\n"
            f"Session Log: {log_path.name if log_path else 'Unavailable'}\n"
        )

    def diagnostic_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "application": {
                "name": "Streamhouse Hub",
                "version": VERSION,
                "build": (
                    "Packaged Windows build"
                    if getattr(sys, "frozen", False)
                    else "Development build"
                ),
                "session_id": self.session_id,
                "session_started": self.started_at.isoformat(),
                "previous_shutdown": self.previous_shutdown,
                "previous_session_id": (
                    str(self.previous_session.get("session_id", "Unknown"))
                    if self.previous_session is not None
                    else "Unavailable"
                ),
            },
            "system": {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "qt": self._qt_version(),
            },
        }
        try:
            data["state"] = sanitize_support_data(dict(self._state_provider()))
        except Exception as error:
            data["state"] = {"status": f"Unavailable: {type(error).__name__}"}
        return data

    def diagnostic_summary(self) -> str:
        data = self.diagnostic_data()
        app = data["application"]
        system = data["system"]
        lines = [
            "Streamhouse Hub Support Diagnostics",
            "",
            f"Hub Version: {app['version']}",
            f"Build: {app['build']}",
            f"Session: {app['session_id']}",
            f"Session Started: {app['session_started']}",
            f"Previous Shutdown: {app['previous_shutdown']}",
            f"Previous Session: {app['previous_session_id']}",
            "",
            "System:",
            f"Windows: {system['platform']}",
            f"Architecture: {system['architecture']}",
            f"Python: {system['python']}",
        ]
        self._append_summary(lines, data.get("state", {}))
        return sanitize_support_text("\n".join(lines))

    @classmethod
    def _append_summary(cls, lines: list[str], values: Mapping[str, Any]) -> None:
        for heading, content in values.items():
            heading_text = str(heading).replace("_", " ").title()
            lines.extend(("", f"{heading_text}:"))
            if isinstance(content, Mapping):
                for key, value in content.items():
                    lines.append(f"{str(key).replace('_', ' ').title()}: {value}")
            else:
                lines.append(str(content))

    def create_support_bundle(self, destination: Path | None = None) -> Path:
        if destination is None:
            destination = self.support_directory / (
                f"StreamhouseHub-Support-{self._stamp()}-{self.session_id[:8]}.zip"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        Logger.flush()
        data = self.diagnostic_data()
        with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
            archive.writestr("diagnostics.txt", self.diagnostic_summary())
            for name in ("application", "system", "state"):
                archive.writestr(
                    f"diagnostics/{'app' if name == 'application' else name}.json",
                    json.dumps(sanitize_support_data(data.get(name, {})), indent=2),
                )
            current = Logger.session_log_path()
            for index, path in enumerate(self._selected_session_logs(current)):
                label = (
                    "current-session.log"
                    if index == 0 and path == current
                    else "previous-session.log"
                )
                archive.writestr(
                    f"logs/{label}",
                    self._support_log_text(path),
                )
            for artifact in self._selected_crash_artifacts():
                archive.writestr(
                    f"crashes/{artifact.name}",
                    sanitize_support_text(
                        artifact.read_text(encoding="utf-8", errors="replace")
                    ),
                )
        return destination

    def _selected_crash_artifacts(self) -> list[Path]:
        selected: list[Path] = []

        def add(path: Path | None) -> None:
            if path is not None and path.exists() and path not in selected:
                selected.append(path)

        add(self.previous_fault_path)
        if self.previous_session is not None:
            prior_prefix = str(self.previous_session.get("session_id", ""))[:8]
            if prior_prefix:
                add(
                    self._latest(
                        self.crashes_directory,
                        f"StreamhouseHub-Crash-*-{prior_prefix}.log",
                    )
                )
        add(self._latest(self.crashes_directory, "StreamhouseHub-Crash-*.log"))
        if self._fault_path is not None and self._fault_path.exists():
            try:
                current_text = self._fault_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                current_text = ""
            if self._artifact_has_captured_fault(current_text):
                add(self._fault_path)
        latest_fault = self._latest(
            self.crashes_directory, "StreamhouseHub-Fault-*.log"
        )
        if latest_fault != self._fault_path:
            add(latest_fault)
        return selected[:4]

    def _selected_session_logs(self, current: Path | None) -> list[Path]:
        logs = sorted(
            self.logs_directory.glob("StreamhouseHub-*.log"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        selected: list[Path] = []
        if current is not None and current.exists():
            selected.append(current)
        selected.extend(path for path in logs if path != current)
        return selected[:2]

    @staticmethod
    def _support_log_text(path: Path) -> str:
        """Copy diagnostic severities only, never a general activity transcript."""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        diagnostic_lines = [
            line
            for line in lines
            if "[ WARNING ]" in line
            or "[  ERROR  ]" in line
            or "[CRITICAL ]" in line
        ]
        return sanitize_support_text("\n".join(diagnostic_lines[-1000:]))

    def clean_shutdown(self) -> None:
        self.checkpoint("clean shutdown completed")
        marker_cleared = not self.marker_path.exists()
        try:
            if self.marker_path.exists():
                marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
                if marker.get("session_id") == self.session_id:
                    self.marker_path.unlink()
                    marker_cleared = True
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.uninstall_hooks(remove_fault_artifact=marker_cleared)

    def uninstall_hooks(self, *, remove_fault_artifact: bool = False) -> None:
        if self._qt_handler_installed:
            try:
                from PySide6.QtCore import qInstallMessageHandler

                qInstallMessageHandler(self._original_qt_handler)
            finally:
                self._qt_handler_installed = False
                self._original_qt_handler = None
        if self._original_sys_hook is not None:
            sys.excepthook = self._original_sys_hook
            threading.excepthook = self._original_thread_hook
            sys.unraisablehook = self._original_unraisable_hook
            self._original_sys_hook = None
        self._close_fault_file(disable=True)
        if self._fault_path is not None:
            try:
                if remove_fault_artifact and self._fault_path.exists():
                    self._fault_path.unlink()
            except OSError:
                pass
            self._fault_path = None
            self._rotate(
                self.crashes_directory,
                "StreamhouseHub-Fault-*.log",
                self.CRASH_RETENTION,
            )

    def _close_fault_file(self, *, disable: bool) -> None:
        if self._fault_file is None:
            return
        with self._fault_write_lock:
            fault_file = self._fault_file
            try:
                if disable:
                    try:
                        faulthandler.disable()
                    except RuntimeError:
                        pass
                try:
                    fault_file.flush()
                except (OSError, ValueError):
                    pass
                try:
                    fault_file.close()
                except (OSError, ValueError):
                    pass
            finally:
                self._fault_file = None

    @staticmethod
    def _qt_version() -> str:
        try:
            from PySide6.QtCore import qVersion

            return qVersion()
        except ImportError:
            return "Unavailable"

    def _stamp(self) -> str:
        return self._now().astimezone(timezone.utc).strftime(
            "%Y-%m-%d-%H%M%S-%f"
        )

    @staticmethod
    def _latest(directory: Path, pattern: str) -> Path | None:
        values = sorted(
            directory.glob(pattern),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return values[0] if values else None

    @classmethod
    def _rotate(cls, directory: Path, pattern: str, keep: int) -> None:
        values = sorted(
            directory.glob(pattern),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for obsolete in values[max(keep, 0):]:
            try:
                obsolete.unlink()
            except OSError:
                pass
