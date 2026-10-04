from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import PythonScriptTask
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import (
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.routines import RoutineStore
from products.hub.automation.script_context import (
    parse_script_bridge,
    script_bridge_command,
    script_bridge_environment,
)
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_providers import context_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.automation.variable_tasks import RunRoutineTask


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
            context={
                "user": "Test Viewer", "channel": "samplechannel",
                "user.display_name": "Test Viewer",
            },
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
            self._task(script, arguments='"{user.display_name}"'),
            self.trigger,
        )

        self.assertTrue(result.succeeded)
        payload = json.loads(result.detail.splitlines()[-1])
        self.assertEqual(payload["user"], "Test Viewer")
        self.assertEqual(payload["context"]["channel"], "samplechannel")
        self.assertEqual(payload["argument"], "Test Viewer")

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

    def test_hub_outputs_are_canonical_typed_and_last_write_wins(self) -> None:
        script = self.root / "outputs.py"
        script.write_text(
            "hub.set_output('song', 'A')\n"
            "hub.set_output('song', 'B')\n"
            "hub.set_output('count', 4)\n"
            "hub.set_output('live', True)\n"
            "hub.set_output('volume', 0.75)\n"
            "print(hub.get_variable('automation.song'))\n",
            encoding="utf-8",
        )

        result = PythonScriptTask().execute(self._task(script), self.trigger)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.trigger.context["automation.song"], "B")
        self.assertEqual(self.trigger.context["automation.count"], "4")
        self.assertEqual(self.trigger.context["automation.live"], "true")
        self.assertEqual(self.trigger.context["automation.volume"], "0.75")
        self.assertTrue(result.detail.endswith("B"))

    def test_hub_get_variable_uses_current_canonical_context(self) -> None:
        registry = VariableRegistry()
        registry.register(context_provider())
        self.trigger.context["command_data"] = "requested track"
        script = self.root / "read.py"
        script.write_text(
            "print(hub.get_variable('user.display_name'))\n"
            "print(hub.get_variable('command.data'))\n",
            encoding="utf-8",
        )

        result = PythonScriptTask(registry).execute(
            self._task(script),
            self.trigger,
        )

        self.assertTrue(result.succeeded)
        self.assertIn("Test Viewer", result.detail)
        self.assertIn("requested track", result.detail)

    def test_unavailable_variable_and_invalid_output_fail_without_partial_state(self) -> None:
        registry = VariableRegistry()
        registry.register(context_provider())
        unavailable_script = self.root / "unavailable.py"
        unavailable_script.write_text(
            "hub.get_variable('command.data')\n",
            encoding="utf-8",
        )
        unavailable = PythonScriptTask(registry).execute(
            self._task(unavailable_script),
            self.trigger,
        )
        self.assertFalse(unavailable.succeeded)
        self.assertIn(
            "Variable is not available in this execution context: command.data",
            unavailable.detail,
        )

        invalid_script = self.root / "invalid.py"
        invalid_script.write_text(
            "hub.set_output('valid', 'before')\n"
            "hub.set_output('automation.invalid', 'after')\n",
            encoding="utf-8",
        )
        invalid = PythonScriptTask().execute(
            self._task(invalid_script),
            self.trigger,
        )
        self.assertFalse(invalid.succeeded)
        self.assertIn("Invalid automation output name", invalid.detail)
        self.assertNotIn("automation.valid", self.trigger.context)

    def test_hub_log_uses_injected_central_log_path_and_log_failure_is_safe(self) -> None:
        script = self.root / "log.py"
        script.write_text("hub.log('Found current track')\n", encoding="utf-8")
        messages: list[str] = []

        result = PythonScriptTask(log_writer=messages.append).execute(
            self._task(script),
            self.trigger,
        )
        safe_result = PythonScriptTask(
            log_writer=lambda _message: (_ for _ in ()).throw(OSError("log"))
        ).execute(self._task(script), self.trigger)

        self.assertTrue(result.succeeded)
        self.assertEqual(messages, ["Found current track"])
        self.assertTrue(safe_result.succeeded)

    def test_default_hub_log_routes_through_script_logger_source(self) -> None:
        script = self.root / "central_log.py"
        script.write_text("hub.log('Now Playing ready')\n", encoding="utf-8")

        with patch("products.hub.automation.core_tasks.Logger.info") as info:
            result = PythonScriptTask().execute(self._task(script), self.trigger)

        self.assertTrue(result.succeeded)
        info.assert_called_once_with("Now Playing ready", source="SCRIPT")

    def test_context_rejects_use_after_script_execution(self) -> None:
        script = self.root / "closed.py"
        script.write_text(
            "import atexit\n"
            "saved_hub = hub\n"
            "def after():\n"
            "    try:\n"
            "        saved_hub.get_variable('user.display_name')\n"
            "    except RuntimeError as error:\n"
            "        print(str(error))\n"
            "atexit.register(after)\n",
            encoding="utf-8",
        )

        result = PythonScriptTask().execute(self._task(script), self.trigger)

        self.assertTrue(result.succeeded)
        self.assertIn("Script execution context is no longer active", result.detail)

    def test_parallel_bridge_processes_keep_same_named_outputs_isolated(self) -> None:
        script = self.root / "isolated.py"
        script.write_text(
            "hub.set_output('song', hub.get_variable('user.display_name'))\n",
            encoding="utf-8",
        )
        processes = []
        tokens = []
        for viewer in ("Viewer A", "Viewer B"):
            token, additions = script_bridge_environment(
                {"user.display_name": viewer}
            )
            environment = dict(os.environ)
            environment.update(additions)
            process = subprocess.Popen(
                script_bridge_command([sys.executable], str(script), []),
                cwd=self.root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            tokens.append(token)

        payloads = []
        for process, token in zip(processes, tokens, strict=True):
            _stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
            payload, ordinary_stderr = parse_script_bridge(stderr, token)
            self.assertEqual(ordinary_stderr, "")
            self.assertIsNotNone(payload)
            payloads.append(payload)

        self.assertEqual(payloads[0].outputs["song"], "Viewer A")
        self.assertEqual(payloads[1].outputs["song"], "Viewer B")

    def test_outputs_flow_to_later_and_nested_tasks_then_clear_between_roots(self) -> None:
        script = self.root / "root_output.py"
        script.write_text(
            "hub.set_output('song', 'Inevitable Struggle')\n",
            encoding="utf-8",
        )
        routines = RoutineStore(self.root / "routines.json")
        variables = CustomVariableStore(self.root / "variables.json")
        variables.load()
        registry = VariableRegistry()
        registry.register(context_provider())
        tasks = TaskRegistry()
        tasks.register(PythonScriptTask(registry))
        captured: list[dict[str, str]] = []

        class CaptureTask:
            task_type = "test.capture_script_output"

            def execute(self, task, trigger):
                captured.append(dict(trigger.context))
                return TaskExecutionResult(
                    task.task_id,
                    task.task_type,
                    True,
                    "captured",
                )

        tasks.register(CaptureTask())
        service = AutomationService(
            routines,
            tasks,
            variables,
            variable_registry=registry,
        )
        tasks.register(
            RunRoutineTask(service.run_nested_routine, service.routine_name)
        )
        nested = routines.add("Nested")
        routines.add_task(
            nested.routine_id,
            task_type=CaptureTask.task_type,
            name="Capture nested output",
        )
        root = routines.add("Root")
        routines.add_task(
            root.routine_id,
            task_type=PythonScriptTask.task_type,
            name="Publish song",
            config=self._task(script).config,
        )
        routines.add_task(
            root.routine_id,
            task_type=RunRoutineTask.task_type,
            name="Run nested",
            config={"routine_id": nested.routine_id},
        )
        routines.add_task(
            root.routine_id,
            task_type=CaptureTask.task_type,
            name="Capture root output",
        )
        clean_root = routines.add("Clean root")
        routines.add_task(
            clean_root.routine_id,
            task_type=CaptureTask.task_type,
            name="Capture clean context",
        )

        first = service.run_routine(root.routine_id)
        second = service.run_routine(clean_root.routine_id)

        self.assertTrue(first.succeeded)
        self.assertTrue(second.succeeded)
        self.assertEqual(captured[0]["automation.song"], "Inevitable Struggle")
        self.assertEqual(captured[1]["automation.song"], "Inevitable Struggle")
        self.assertNotIn("automation.song", captured[2])


if __name__ == "__main__":
    unittest.main()
