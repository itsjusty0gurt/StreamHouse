from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from products.hub.core.diagnostics import DiagnosticsService, sanitize_support_text
from shared.streamhouse_runtime.logger import Logger


class DiagnosticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original_logger = Logger._logger
        self.original_session_id = Logger._session_id
        self.original_session_log_path = Logger._session_log_path

    def tearDown(self) -> None:
        Logger.shutdown()
        Logger._logger = self.original_logger
        Logger._session_id = self.original_session_id
        Logger._session_log_path = self.original_session_log_path
        self.temporary.cleanup()

    def test_clean_and_abnormal_session_lifecycle(self) -> None:
        first = DiagnosticsService(self.root, process_checker=lambda _pid: False)
        self.assertTrue(first.session_id)
        self.assertTrue(first.marker_path.exists())
        first.clean_shutdown()

        clean = DiagnosticsService(self.root, process_checker=lambda _pid: False)
        self.assertEqual(clean.previous_shutdown, "Clean")
        previous_id = clean.session_id

        abnormal = DiagnosticsService(self.root, process_checker=lambda _pid: False)
        self.assertTrue(abnormal.previous_shutdown_abnormal)
        self.assertEqual(abnormal.previous_session["session_id"], previous_id)
        abnormal.clean_shutdown()

    def test_live_previous_process_is_not_called_abnormal(self) -> None:
        first = DiagnosticsService(self.root)
        second = DiagnosticsService(self.root, process_checker=lambda _pid: True)

        self.assertEqual(second.previous_shutdown, "Active")
        self.assertFalse(second.previous_shutdown_abnormal)
        first.clean_shutdown()
        second.clean_shutdown()

    def test_session_logging_uses_one_id_and_rotates(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        for index in range(12):
            path = diagnostics.logs_directory / f"StreamhouseHub-old-{index:02d}.log"
            path.write_text("old", encoding="utf-8")
            path.touch()
        Logger.setup(
            session_id=diagnostics.session_id,
            product_name="StreamhouseHub",
            log_directory=diagnostics.logs_directory,
            retained_sessions=10,
        )
        Logger.info("session ready", source="TEST")
        Logger.flush()

        current = Logger.session_log_path()
        self.assertIn(diagnostics.session_id[:8], current.name)
        self.assertIn(diagnostics.session_id, current.read_text(encoding="utf-8"))
        self.assertLessEqual(
            len(list(diagnostics.logs_directory.glob("StreamhouseHub-*.log"))),
            10,
        )
        diagnostics.clean_shutdown()

    def test_support_bundle_is_sanitized_and_does_not_include_configuration(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        current = diagnostics.logs_directory / "StreamhouseHub-current.log"
        previous = diagnostics.logs_directory / "StreamhouseHub-previous.log"
        secret = "secret-token-value"
        current.write_text(
            f"[ WARNING ] Authorization: Bearer {secret}\n"
            f"[  ERROR  ] obs_password=hunter2 path={Path.home()}\\AppData\\Local",
            encoding="utf-8",
        )
        previous.write_text("previous safe log", encoding="utf-8")
        backup = self.root / "backups" / "private-backup.zip"
        backup.parent.mkdir()
        backup.write_text("PRIVATE CONFIGURATION", encoding="utf-8")
        Logger._session_log_path = current
        diagnostics.set_state_provider(
            lambda: {
                "twitch": {"connected": True},
                "automation": {"routines": 4},
                "storage_schemas": {"commands": 6},
                "accidental": {"access_token": secret},
            }
        )

        bundle = diagnostics.create_support_bundle()

        with ZipFile(bundle) as archive:
            names = set(archive.namelist())
            state = json.loads(archive.read("diagnostics/state.json"))
            combined = "\n".join(
                archive.read(name).decode("utf-8", errors="replace")
                for name in names
            )
        self.assertIn("diagnostics.txt", names)
        self.assertIn("diagnostics/app.json", names)
        self.assertIn("diagnostics/system.json", names)
        self.assertIn("diagnostics/state.json", names)
        self.assertIn("logs/current-session.log", names)
        self.assertIn("logs/previous-session.log", names)
        self.assertNotIn(secret, combined)
        self.assertNotIn("hunter2", combined)
        self.assertIn("<REDACTED>", combined)
        self.assertIn("<USER_HOME>", combined)
        self.assertEqual(state["accidental"]["access_token"], "<REDACTED>")
        self.assertNotIn("backups", " ".join(names))
        self.assertEqual(
            current.read_text(encoding="utf-8").splitlines()[0],
            f"[ WARNING ] Authorization: Bearer {secret}",
        )
        diagnostics.clean_shutdown()

    def test_crash_report_is_sanitized_and_rotated(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        test_logger = logging.Logger("diagnostics-test")
        test_logger.addHandler(logging.NullHandler())
        Logger._logger = test_logger
        try:
            raise RuntimeError("access_token=do-not-share")
        except RuntimeError as error:
            report = diagnostics.record_exception(
                "Test failure", type(error), error, error.__traceback__
            )

        text = report.read_text(encoding="utf-8")
        self.assertIn(diagnostics.session_id, text)
        self.assertIn("<REDACTED>", text)
        self.assertNotIn("do-not-share", text)
        for index in range(7):
            path = diagnostics.crashes_directory / f"StreamhouseHub-Crash-old-{index}.log"
            path.write_text("old", encoding="utf-8")
        diagnostics._rotate(
            diagnostics.crashes_directory,
            "StreamhouseHub-Crash-*.log",
            diagnostics.CRASH_RETENTION,
        )
        self.assertLessEqual(
            len(list(diagnostics.crashes_directory.glob("StreamhouseHub-Crash-*.log"))),
            diagnostics.CRASH_RETENTION,
        )
        diagnostics.clean_shutdown()

    def test_exception_hooks_and_faulthandler_install_and_restore(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        with patch("products.hub.core.diagnostics.faulthandler.enable") as enable, patch(
            "products.hub.core.diagnostics.faulthandler.disable"
        ) as disable:
            diagnostics.install_exception_hooks()
            self.assertIs(sys.excepthook.__self__, diagnostics)
            self.assertIs(threading.excepthook.__self__, diagnostics)
            self.assertIs(sys.unraisablehook.__self__, diagnostics)
            enable.assert_called_once()
            diagnostics.clean_shutdown()
            disable.assert_called_once()

    def test_python_hook_routes_cover_main_thread_worker_and_unraisable(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        diagnostics._original_sys_hook = Mock()
        diagnostics._original_thread_hook = Mock()
        diagnostics._original_unraisable_hook = Mock()
        error = RuntimeError("boom")
        thread_args = Mock(
            exc_type=RuntimeError,
            exc_value=error,
            exc_traceback=None,
            thread=Mock(name="worker"),
        )
        unraisable_args = Mock(exc_value=error, exc_traceback=None)
        with patch.object(diagnostics, "record_exception") as record:
            diagnostics._handle_sys_exception(RuntimeError, error, None)
            diagnostics._handle_thread_exception(thread_args)
            diagnostics._handle_unraisable(unraisable_args)
        self.assertEqual(record.call_count, 3)

    def test_qt_warning_is_mapped_into_hub_logger(self) -> None:
        from PySide6.QtCore import QtMsgType

        diagnostics = DiagnosticsService(self.root)
        handlers = []

        def install(handler):
            handlers.append(handler)
            return None

        with patch("PySide6.QtCore.qInstallMessageHandler", side_effect=install), patch.object(
            Logger, "warning"
        ) as warning:
            diagnostics.install_qt_message_handler()
            handlers[0](QtMsgType.QtWarningMsg, None, "Qt warning")
            diagnostics.clean_shutdown()
        warning.assert_called_once_with("Qt warning", source="QT")

    def test_summary_uses_safe_provider_state(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        diagnostics.set_state_provider(
            lambda: {
                "twitch": {"chat": "Connected", "eventsub": "Connected"},
                "obs": {"connected": True},
                "automation": {"routines": 12, "queues": 2},
                "storage_schemas": {"commands": 6},
            }
        )
        summary = diagnostics.diagnostic_summary()

        self.assertIn("Streamhouse Hub Support Diagnostics", summary)
        self.assertIn(diagnostics.session_id, summary)
        self.assertIn("Chat: Connected", summary)
        self.assertIn("Routines: 12", summary)
        self.assertNotIn("access_token", summary)
        diagnostics.clean_shutdown()

    def test_sanitizer_covers_headers_keys_urls_and_home(self) -> None:
        value = (
            f"Authorization: Bearer abc Authorization: OAuth oauth-secret "
            f"access_token=def refresh_token='ghi' "
            f"api_key=jkl relay_secret=mno https://x.test?a=1&token=pqr "
            f'https://x.test?client_secret=query-secret '
            f'\"client_secret\": \"stu\" {Path.home()}\\file'
        )
        safe = sanitize_support_text(value)
        for secret in (
            "abc",
            "oauth-secret",
            "def",
            "ghi",
            "jkl",
            "mno",
            "pqr",
            "query-secret",
            "stu",
        ):
            self.assertNotIn(secret, safe)
        self.assertIn("<USER_HOME>", safe)


if __name__ == "__main__":
    unittest.main()
