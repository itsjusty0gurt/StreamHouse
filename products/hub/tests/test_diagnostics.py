from __future__ import annotations

import json
import logging
import subprocess
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
        # Other persistence tests intentionally exercise recovery logging.
        # Diagnostics tests own an isolated logger session regardless of order.
        Logger.shutdown()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        Logger.shutdown()
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
                "accidental": {
                    "access_token": secret,
                    "obs_password": "obs-secret",
                    "X-Streamhouse-Key": "relay-secret",
                },
                "message_text": "private viewer diagnostic sentinel",
                "raw_payload": {
                    "event": {"message": "private raw event sentinel"}
                },
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
        self.assertNotIn("obs-secret", combined)
        self.assertNotIn("relay-secret", combined)
        self.assertNotIn("private viewer diagnostic sentinel", combined)
        self.assertNotIn("private raw event sentinel", combined)
        self.assertIn("<PRIVATE CONTENT OMITTED>", combined)
        self.assertIn("<REDACTED>", combined)
        self.assertIn("<USER_HOME>", combined)
        self.assertEqual(state["accidental"]["access_token"], "<REDACTED>")
        self.assertEqual(state["accidental"]["obs_password"], "<REDACTED>")
        self.assertEqual(state["accidental"]["X-Streamhouse-Key"], "<REDACTED>")
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
        self.assertIn("RuntimeError", text)
        self.assertIn("exception message omitted for privacy", text)
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
        with patch("products.hub.core.diagnostics.faulthandler.enable") as enable, patch(
            "products.hub.core.diagnostics.faulthandler.disable"
        ) as disable:
            diagnostics = DiagnosticsService(self.root)
            diagnostics.install_exception_hooks()
            self.assertIs(sys.excepthook.__self__, diagnostics)
            self.assertIs(threading.excepthook.__self__, diagnostics)
            self.assertIs(sys.unraisablehook.__self__, diagnostics)
            enable.assert_called_once()
            diagnostics.clean_shutdown()
            disable.assert_called_once()

    def test_python_hook_routes_cover_main_thread_worker_and_unraisable(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        diagnostics.install_exception_hooks()
        error = RuntimeError("boom")
        thread_args = Mock(
            exc_type=RuntimeError,
            exc_value=error,
            exc_traceback=None,
            thread=Mock(name="worker"),
        )
        unraisable_args = Mock(
            exc_type=RuntimeError,
            exc_value=error,
            exc_traceback=None,
        )
        with patch.object(diagnostics, "record_exception") as record, patch.object(
            diagnostics, "_original_sys_hook", Mock()
        ), patch.object(
            diagnostics, "_original_thread_hook", Mock()
        ), patch.object(
            diagnostics, "_original_unraisable_hook", Mock()
        ):
            diagnostics._handle_sys_exception(RuntimeError, error, None)
            diagnostics._handle_thread_exception(thread_args)
            diagnostics._handle_unraisable(unraisable_args)
        self.assertEqual(record.call_count, 3)
        diagnostics.clean_shutdown()

    def test_worker_and_unraisable_hooks_write_crash_artifacts(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        diagnostics.install_exception_hooks()
        error = RuntimeError("refresh_token=private-worker-token")
        thread_args = Mock(
            exc_type=RuntimeError,
            exc_value=error,
            exc_traceback=None,
            thread=Mock(name="worker"),
        )
        unraisable_args = Mock(
            exc_type=RuntimeError,
            exc_value=error,
            exc_traceback=None,
        )
        try:
            with patch.object(diagnostics, "_original_thread_hook", Mock()), patch.object(
                diagnostics, "_original_unraisable_hook", Mock()
            ):
                diagnostics._handle_thread_exception(thread_args)
                diagnostics._handle_unraisable(unraisable_args)
            reports = list(
                self.root.glob("crashes/StreamhouseHub-Crash-*.log")
            )
            self.assertEqual(len(reports), 2)
            combined = "\n".join(
                report.read_text(encoding="utf-8") for report in reports
            )
            self.assertIn("Unhandled worker-thread exception", combined)
            self.assertIn("Unraisable Python exception", combined)
            self.assertNotIn("private-worker-token", combined)
        finally:
            diagnostics.clean_shutdown()

    def test_fault_artifact_is_precreated_flushed_and_removed_on_clean_shutdown(self) -> None:
        diagnostics = DiagnosticsService(self.root)
        artifact = diagnostics._fault_path

        self.assertIsNotNone(artifact)
        self.assertTrue(artifact.exists())
        text = artifact.read_text(encoding="utf-8")
        self.assertIn(diagnostics.session_id, text)
        self.assertIn("Checkpoint: diagnostics initialized", text)
        self.assertTrue(diagnostics.marker_path.exists())

        diagnostics.clean_shutdown()

        self.assertFalse(diagnostics.marker_path.exists())
        self.assertFalse(artifact.exists())

    def test_fault_capture_precedes_active_marker(self) -> None:
        events: list[str] = []
        original_start = DiagnosticsService._start_fault_capture
        original_marker = DiagnosticsService._write_active_marker

        def start(service) -> None:
            events.append("fault-capture")
            original_start(service)

        def marker(service) -> None:
            events.append("active-marker")
            original_marker(service)

        with patch.object(DiagnosticsService, "_start_fault_capture", start), patch.object(
            DiagnosticsService, "_write_active_marker", marker
        ):
            diagnostics = DiagnosticsService(self.root)
        try:
            self.assertLess(
                events.index("fault-capture"), events.index("active-marker")
            )
        finally:
            diagnostics.clean_shutdown()

    def test_fault_retention_keeps_five_completed_artifacts_plus_active(self) -> None:
        crashes = self.root / "crashes"
        crashes.mkdir(parents=True)
        for index in range(7):
            path = crashes / f"StreamhouseHub-Fault-old-{index}.log"
            path.write_text("abnormal", encoding="utf-8")
            path.touch()

        diagnostics = DiagnosticsService(self.root)
        active = diagnostics._fault_path
        try:
            self.assertEqual(
                len(list(crashes.glob("StreamhouseHub-Fault-*.log"))),
                diagnostics.CRASH_RETENTION + 1,
            )
        finally:
            diagnostics.clean_shutdown()

        self.assertFalse(active.exists())
        self.assertEqual(
            len(list(crashes.glob("StreamhouseHub-Fault-*.log"))),
            diagnostics.CRASH_RETENTION,
        )

    def test_uncaught_main_thread_exception_creates_sanitized_crash_report(self) -> None:
        code = (
            "from pathlib import Path\n"
            "from products.hub.core.diagnostics import DiagnosticsService\n"
            "from shared.streamhouse_runtime.logger import Logger\n"
            f"root = Path({str(self.root)!r})\n"
            "diagnostics = DiagnosticsService(root)\n"
            "Logger.setup(session_id=diagnostics.session_id, product_name='StreamhouseHub', "
            "log_directory=diagnostics.logs_directory)\n"
            "diagnostics.install_exception_hooks()\n"
            "raise RuntimeError('access_token=private-test-token')\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        reports = list(self.root.glob("crashes/StreamhouseHub-Crash-*.log"))
        self.assertEqual(len(reports), 1)
        report_text = reports[0].read_text(encoding="utf-8")
        self.assertIn("RuntimeError", report_text)
        self.assertNotIn("private-test-token", report_text)

        next_session = DiagnosticsService(
            self.root, process_checker=lambda _pid: False
        )
        try:
            self.assertTrue(next_session.previous_shutdown_abnormal)
            fault_text = next_session.previous_fault_path.read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "in-process exception or fault output was captured",
                fault_text,
            )
        finally:
            next_session.clean_shutdown()

    def test_os_exit_leaves_correlated_abnormal_artifact_for_support(self) -> None:
        secret = "access_token=abnormal-secret"
        code = (
            "import os\n"
            "from pathlib import Path\n"
            "from products.hub.core.diagnostics import DiagnosticsService\n"
            f"diagnostics = DiagnosticsService(Path({str(self.root)!r}))\n"
            f"diagnostics._write_fault_text({secret!r} + '\\n', durable=True)\n"
            "os._exit(23)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[3],
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 23)

        diagnostics = DiagnosticsService(
            self.root, process_checker=lambda _pid: False
        )
        try:
            self.assertTrue(diagnostics.previous_shutdown_abnormal)
            self.assertIsNotNone(diagnostics.previous_fault_path)
            self.assertIn(
                diagnostics.previous_session["session_id"],
                diagnostics.diagnostic_summary(),
            )
            previous_text = diagnostics.previous_fault_path.read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "Abnormal termination detected; no in-process exception was captured.",
                previous_text,
            )
            bundle = diagnostics.create_support_bundle()
            with ZipFile(bundle) as archive:
                fault_names = [
                    name for name in archive.namelist() if name.startswith("crashes/")
                ]
                combined = "\n".join(
                    archive.read(name).decode("utf-8", errors="replace")
                    for name in fault_names
                )
            self.assertTrue(fault_names)
            self.assertIn(diagnostics.previous_session["session_id"], combined)
            self.assertIn("<REDACTED>", combined)
            self.assertNotIn("abnormal-secret", combined)
        finally:
            diagnostics.clean_shutdown()

    @unittest.skipUnless(sys.platform == "win32", "Windows process termination")
    def test_forced_process_termination_leaves_precreated_fault_artifact(self) -> None:
        code = (
            "import time\n"
            "from pathlib import Path\n"
            "from products.hub.core.diagnostics import DiagnosticsService\n"
            f"diagnostics = DiagnosticsService(Path({str(self.root)!r}))\n"
            "print('READY', flush=True)\n"
            "time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[3],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "READY")
            child.terminate()
            child.wait(timeout=20)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=20)

        diagnostics = DiagnosticsService(
            self.root, process_checker=lambda _pid: False
        )
        try:
            self.assertTrue(diagnostics.previous_shutdown_abnormal)
            text = diagnostics.previous_fault_path.read_text(encoding="utf-8")
            self.assertIn("Checkpoint: diagnostics initialized", text)
            self.assertIn("no in-process exception was captured", text)
        finally:
            diagnostics.clean_shutdown()

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
            f"api_key=jkl relay_secret=mno X-Streamhouse-Key=modern-relay "
            f"STREAMHOUSE_RELAY_KEYS=environment-secret "
            f"https://user:pass@x.test/a#token=fragment-secret "
            f"wss://obs-user:obs-pass@obs.test/socket "
            f"https://auth.test/callback?code=oauth-code&safe=value "
            f"https://x.test?a=1&token=pqr "
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
            "modern-relay",
            "environment-secret",
            "user:pass",
            "fragment-secret",
            "obs-user:obs-pass",
            "oauth-code",
            "pqr",
            "query-secret",
            "stu",
        ):
            self.assertNotIn(secret, safe)
        self.assertIn("<USER_HOME>", safe)
        self.assertIn("safe=value", safe)


if __name__ == "__main__":
    unittest.main()
