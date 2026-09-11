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
from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.paths import user_data_root
from shared.streamhouse_runtime.version import VERSION


_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(authorization\s*[:=]\s*(?:bearer|oauth)\s+)([^\s,;]+)"
    ),
    re.compile(
        r"(?i)((?:\"|')?(?:authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|"
        r"client[_ -]?secret|password|obs[_ -]?password|relay[_ -]?(?:key|secret)|"
        r"sally_relay_(?:base|keys|db)|cookie|session[_ -]?secret|"
        r"x-sally-[\w-]+)(?:\"|')?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(
        r"(?i)([?&](?:access_token|refresh_token|api_key|client_secret|key|token|"
        r"secret|password)=)"
        r"([^&#\s]+)"
    ),
)
_SECRET_KEY = re.compile(
    r"(?i)^(?:authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|"
    r"client[_ -]?secret|password|obs[_ -]?password|relay[_ -]?(?:key|secret)|"
    r"sally_relay_(?:base|keys|db)|cookie|session[_ -]?secret|x-sally-[\w-]+)$"
)


def sanitize_support_text(value: object, *, home: Path | None = None) -> str:
    """Redact likely credentials and local user-home paths from a copied artifact."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1<REDACTED>", text)
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
                if _SECRET_KEY.fullmatch(str(key))
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
        self._fault_file = None
        self._fault_path: Path | None = None
        self._load_previous_marker()
        self._write_active_marker()
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
            },
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
        fault_path = self.crashes_directory / (
            f"StreamhouseHub-Fault-{self._stamp()}-{self.session_id[:8]}.log"
        )
        self._fault_path = fault_path
        try:
            self._fault_file = fault_path.open("a", encoding="utf-8")
            faulthandler.enable(file=self._fault_file, all_threads=True)
        except (OSError, RuntimeError) as error:
            Logger.warning(
                f"Python faulthandler could not be enabled: {type(error).__name__}",
                source="SYSTEM",
            )
            if self._fault_file is not None:
                self._fault_file.close()
                self._fault_file = None

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
            type(args.exc_value),
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
        rendered = "".join(
            traceback.format_exception(exception_type, exception_value, exception_traceback)
        )
        safe_traceback = sanitize_support_text(rendered)
        Logger.critical(f"{label}:\n{safe_traceback}", source="SYSTEM")
        report = self.crashes_directory / (
            f"StreamhouseHub-Crash-{self._stamp()}-{self.session_id[:8]}.log"
        )
        try:
            report.write_text(
                self._crash_header(label) + "\n" + safe_traceback,
                encoding="utf-8",
            )
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
            },
            "system": {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "qt": self._qt_version(),
            },
        }
        try:
            data["state"] = dict(self._state_provider())
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
            crash = self._latest(
                self.crashes_directory, "StreamhouseHub-Crash-*.log"
            )
            if crash is not None:
                archive.writestr(
                    f"crashes/{crash.name}",
                    sanitize_support_text(
                        crash.read_text(encoding="utf-8", errors="replace")
                    ),
                )
            fault = self._latest(
                self.crashes_directory, "StreamhouseHub-Fault-*.log"
            )
            if fault is not None and fault.stat().st_size:
                archive.writestr(
                    f"crashes/{fault.name}",
                    sanitize_support_text(
                        fault.read_text(encoding="utf-8", errors="replace")
                    ),
                )
        return destination

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
        try:
            if self.marker_path.exists():
                marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
                if marker.get("session_id") == self.session_id:
                    self.marker_path.unlink()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.uninstall_hooks()

    def uninstall_hooks(self) -> None:
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
        if self._fault_file is not None:
            try:
                faulthandler.disable()
                self._fault_file.flush()
                self._fault_file.close()
            finally:
                self._fault_file = None
        if self._fault_path is not None:
            try:
                if self._fault_path.exists() and self._fault_path.stat().st_size == 0:
                    self._fault_path.unlink()
            except OSError:
                pass
            self._fault_path = None
            self._rotate(
                self.crashes_directory,
                "StreamhouseHub-Fault-*.log",
                self.CRASH_RETENTION,
            )

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
