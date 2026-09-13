from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from products.hub import hub_main
from products.hub.core.diagnostics import DiagnosticsService
from products.hub.core.single_instance import (
    HubInstanceLock,
    InstanceLockError,
    _data_root_fingerprint,
)


class _FakeDiagnostics:
    NORMAL_LOG_RETENTION = 10
    session_id = "test-session"

    def __init__(self, root: Path, events: list[str]) -> None:
        self.logs_directory = root / "logs"
        self.events = events
        events.append("diagnostics")

    def install_exception_hooks(self) -> None:
        self.events.append("hooks")


class _FakeLock:
    def __init__(
        self,
        events: list[str],
        *,
        result: bool = True,
        error: bool = False,
    ) -> None:
        self.events = events
        self.result = result
        self.error = error
        self.acquired = False

    def try_acquire(self) -> bool:
        self.events.append("lock")
        if self.error:
            raise InstanceLockError("test lock failure")
        self.acquired = self.result
        return self.result

    def release(self) -> None:
        if self.acquired:
            self.events.append("release")
            self.acquired = False


class HubStartupOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @patch("products.hub.hub_main.Logger.info")
    @patch("products.hub.hub_main.Logger.setup")
    def test_lock_precedes_diagnostics_and_writable_composition(
        self,
        _logger_setup,
        _logger_info,
    ) -> None:
        events: list[str] = []
        lock = _FakeLock(events)

        def run(diagnostics: _FakeDiagnostics) -> None:
            self.assertTrue(lock.acquired)
            self.assertIsNotNone(diagnostics)
            events.append("run")

        result = hub_main.main(
            data_root=self.root,
            lock_factory=lambda _root: lock,
            diagnostics_factory=lambda root: _FakeDiagnostics(root, events),
            runner=run,
            notice=lambda _message: self.fail("unexpected notice"),
        )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["lock", "diagnostics", "hooks", "run", "release"])

    @patch("products.hub.hub_main.Logger.info")
    @patch("products.hub.hub_main.Logger.setup")
    def test_duplicate_launch_never_initializes_writable_hub(
        self,
        logger_setup,
        logger_info,
    ) -> None:
        events: list[str] = []
        marker = self.root / "diagnostics" / "active-session.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{"session_id":"primary"}', encoding="utf-8")
        original_marker = marker.read_bytes()
        notices: list[str] = []

        result = hub_main.main(
            data_root=self.root,
            lock_factory=lambda _root: _FakeLock(events, result=False),
            diagnostics_factory=lambda _root: self.fail(
                "duplicate launch created diagnostics"
            ),
            runner=lambda _diagnostics: self.fail(
                "duplicate launch composed writable Hub"
            ),
            notice=notices.append,
        )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["lock"])
        self.assertEqual(marker.read_bytes(), original_marker)
        self.assertIn("already running", notices[0])
        logger_setup.assert_not_called()
        logger_info.assert_not_called()

    @patch("products.hub.hub_main.Logger.info")
    @patch("products.hub.hub_main.Logger.setup")
    def test_lock_failure_fails_closed_before_composition(
        self,
        logger_setup,
        logger_info,
    ) -> None:
        events: list[str] = []
        notices: list[str] = []

        result = hub_main.main(
            data_root=self.root,
            lock_factory=lambda _root: _FakeLock(events, error=True),
            diagnostics_factory=lambda _root: self.fail("diagnostics initialized"),
            runner=lambda _diagnostics: self.fail("Hub composed"),
            notice=notices.append,
        )

        self.assertEqual(result, 1)
        self.assertEqual(events, ["lock"])
        self.assertIn("will not start", notices[0])
        logger_setup.assert_not_called()
        logger_info.assert_not_called()

    @patch("products.hub.hub_main.Logger.info")
    @patch("products.hub.hub_main.Logger.setup")
    def test_ownership_survives_tray_lifetime_and_releases_after_teardown(
        self,
        _logger_setup,
        _logger_info,
    ) -> None:
        events: list[str] = []
        lock = _FakeLock(events)

        def run(_diagnostics: _FakeDiagnostics) -> None:
            events.extend(("window-hidden-to-tray", "writable-teardown-complete"))
            self.assertTrue(lock.acquired)

        hub_main.main(
            data_root=self.root,
            lock_factory=lambda _root: lock,
            diagnostics_factory=lambda root: _FakeDiagnostics(root, events),
            runner=run,
            notice=lambda _message: self.fail("unexpected notice"),
        )

        self.assertEqual(events[-3:], [
            "window-hidden-to-tray",
            "writable-teardown-complete",
            "release",
        ])

    @patch("products.hub.hub_main.Logger.info")
    @patch("products.hub.hub_main.Logger.setup")
    def test_runner_failure_still_releases_ownership(
        self,
        _logger_setup,
        _logger_info,
    ) -> None:
        events: list[str] = []
        lock = _FakeLock(events)

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            hub_main.main(
                data_root=self.root,
                lock_factory=lambda _root: lock,
                diagnostics_factory=lambda root: _FakeDiagnostics(root, events),
                runner=lambda _diagnostics: (_ for _ in ()).throw(
                    RuntimeError("startup failed")
                ),
                notice=lambda _message: self.fail("unexpected notice"),
            )

        self.assertFalse(lock.acquired)
        self.assertEqual(events[-1], "release")


@unittest.skipUnless(sys.platform == "win32", "Windows named mutex behavior")
class WindowsInstanceMutexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        self.temporary_directory.cleanup()

    def _start_owner(self, *, diagnostics: bool = False) -> subprocess.Popen[str]:
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from products.hub.core.single_instance import HubInstanceLock\n"
            f"root = Path({str(self.root)!r})\n"
            "lock = HubInstanceLock(root)\n"
            "assert lock.try_acquire()\n"
        )
        if diagnostics:
            script += (
                "from products.hub.core.diagnostics import DiagnosticsService\n"
                "diagnostics = DiagnosticsService(root)\n"
            )
        script += (
            "print('ACQUIRED', flush=True)\n"
            "sys.stdin.readline()\n"
            "lock.release()\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[3],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        self.assertEqual(process.stdout.readline().strip(), "ACQUIRED")
        return process

    def test_live_owner_cannot_be_stolen_then_clean_release_allows_start(self) -> None:
        owner = self._start_owner()
        contender = HubInstanceLock(self.root)
        self.assertFalse(contender.try_acquire())

        owner.stdin.write("exit\n")
        owner.stdin.flush()
        self.assertEqual(owner.wait(timeout=10), 0, owner.stderr.read())

        successor = HubInstanceLock(self.root)
        self.assertTrue(successor.try_acquire())
        successor.release()

    def test_forced_termination_releases_lock_and_preserves_abnormal_marker(self) -> None:
        owner = self._start_owner(diagnostics=True)
        marker = self.root / "diagnostics" / "active-session.json"
        self.assertTrue(marker.exists())

        owner.terminate()
        owner.wait(timeout=10)

        successor = HubInstanceLock(self.root)
        self.assertTrue(successor.try_acquire())
        diagnostics = DiagnosticsService(self.root)
        try:
            self.assertEqual(diagnostics.previous_shutdown, "Abnormal")
        finally:
            diagnostics.clean_shutdown()
            successor.release()

    def test_different_data_roots_have_independent_ownership(self) -> None:
        other_root = self.root / "other"
        first = HubInstanceLock(self.root)
        second = HubInstanceLock(other_root)
        self.assertNotEqual(first.identity, second.identity)
        self.assertTrue(first.try_acquire())
        self.assertTrue(second.try_acquire())
        second.release()
        first.release()

    def test_same_process_cannot_recursively_claim_same_root(self) -> None:
        first = HubInstanceLock(self.root)
        second = HubInstanceLock(self.root)
        self.assertTrue(first.try_acquire())
        self.assertFalse(second.try_acquire())
        first.release()
        self.assertTrue(second.try_acquire())
        second.release()

    def test_identity_is_deterministic_and_contains_no_data_root(self) -> None:
        first = HubInstanceLock(self.root)
        second = HubInstanceLock(self.root)
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(
            first.identity,
            f"StreamhouseHub-{_data_root_fingerprint(self.root)}",
        )
        self.assertNotIn(self.root.name, first.identity)


if __name__ == "__main__":
    unittest.main()
