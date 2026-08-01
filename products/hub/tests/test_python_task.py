from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import PythonScriptTask
from products.hub.automation.models import TaskDefinition, TriggerEvent


class PythonScriptTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.trigger = TriggerEvent(
            trigger_id="test.python",
            service="twitch",
            trigger_type="command",
            context={"user": "Test Viewer", "channel": "samplechannel"},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _task(self, script: Path, **overrides) -> TaskDefinition:
        config = {
            "script": str(script),
            "python_executable": sys.executable,
            "arguments": "",
            "working_directory": "",
            "timeout_seconds": 5.0,
            "wait_for_completion": True,
            "capture_output": True,
            "stop_on_failure": True,
        }
        config.update(overrides)
        return TaskDefinition(
            task_id="python-task",
            task_type=PythonScriptTask.task_type,
            name="Run test script",
            config=config,
        )

    def test_runs_in_separate_python_and_supplies_trigger_context(self) -> None:
        script = self.root / "context.py"
        script.write_text(
            "import json, os, sys\n"
            "print(json.dumps({'user': os.environ['STREAMHOUSE_USER'], "
            "'context': json.loads(os.environ['STREAMHOUSE_TRIGGER_CONTEXT']), "
            "'argument': sys.argv[1]}))\n",
            encoding="utf-8",
        )

        result = PythonScriptTask().execute(
            self._task(script, arguments='"{user}"'),
            self.trigger,
        )

        self.assertTrue(result.succeeded)
        payload = json.loads(result.detail.splitlines()[-1])
        self.assertEqual(payload["user"], "Test Viewer")
        self.assertEqual(payload["context"]["channel"], "samplechannel")
        self.assertEqual(payload["argument"], "Test Viewer")

    def test_existing_scripts_receive_temporary_sally_environment_aliases(
        self,
    ) -> None:
        environment = PythonScriptTask._environment(self.trigger)

        self.assertEqual(
            environment["SALLY_TRIGGER_CONTEXT"],
            environment["STREAMHOUSE_TRIGGER_CONTEXT"],
        )
        self.assertEqual(
            environment["SALLY_USER"],
            environment["STREAMHOUSE_USER"],
        )

    def test_nonzero_exit_can_stop_or_continue_the_routine(self) -> None:
        script = self.root / "failure.py"
        script.write_text("raise SystemExit(4)\n", encoding="utf-8")
        handler = PythonScriptTask()

        stopped = handler.execute(self._task(script), self.trigger)
        continued = handler.execute(
            self._task(script, stop_on_failure=False), self.trigger
        )

        self.assertFalse(stopped.succeeded)
        self.assertIn("code 4", stopped.detail)
        self.assertTrue(continued.succeeded)
        self.assertIn("routine will continue", continued.detail)

    def test_timeout_kills_script(self) -> None:
        script = self.root / "slow.py"
        script.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")

        result = PythonScriptTask().execute(
            self._task(script, timeout_seconds=0.1), self.trigger
        )

        self.assertFalse(result.succeeded)
        self.assertIn("timed out", result.detail)

    def test_background_mode_returns_after_starting(self) -> None:
        script = self.root / "background.py"
        script.write_text("pass\n", encoding="utf-8")
        with patch("products.hub.automation.core_tasks.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            result = PythonScriptTask().execute(
                self._task(script, wait_for_completion=False), self.trigger
            )

        self.assertTrue(result.succeeded)
        self.assertIn("process 1234", result.detail)
        popen.assert_called_once()

    def test_rejects_missing_or_non_python_files(self) -> None:
        missing = PythonScriptTask().execute(
            self._task(self.root / "missing.py"), self.trigger
        )
        text = self.root / "not-python.txt"
        text.write_text("pass\n", encoding="utf-8")
        wrong_type = PythonScriptTask().execute(self._task(text), self.trigger)

        self.assertFalse(missing.succeeded)
        self.assertIn("not found", missing.detail)
        self.assertFalse(wrong_type.succeeded)
        self.assertIn(".py or .pyw", wrong_type.detail)


if __name__ == "__main__":
    unittest.main()
