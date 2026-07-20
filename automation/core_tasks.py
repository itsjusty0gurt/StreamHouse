from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtCore import (
    QEventLoop,
    QProcess,
    QProcessEnvironment,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QDesktopServices

from automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent


CORE_TASK_LABELS = {
    "core.launch_application": "Core — Launch application",
    "core.close_application": "Core — Close application",
    "core.delay": "Core — Wait / delay",
    "core.wait_for_service": "Core — Wait for service",
    "core.open_target": "Core — Open file, folder, or URL",
    "core.run_python_script": "Core — Run Python script",
}


def _result(task: TaskDefinition, succeeded: bool, detail: str) -> TaskExecutionResult:
    return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)


class LaunchApplicationTask:
    task_type = "core.launch_application"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        executable = str(task.config.get("executable", "")).strip()
        if not executable:
            return _result(task, False, "Choose an application to launch.")
        if bool(task.config.get("only_if_not_running", False)):
            image = Path(executable).name
            check = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if image.casefold() in check.stdout.casefold():
                return _result(task, True, f"{image} is already running.")
        arguments = shlex.split(str(task.config.get("arguments", "")), posix=False)
        working_directory = str(task.config.get("working_directory", "")).strip()
        startupinfo = None
        if os.name == "nt" and bool(task.config.get("start_minimized", False)):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 7
        subprocess.Popen(
            [executable, *arguments],
            cwd=working_directory or None,
            startupinfo=startupinfo,
        )
        return _result(task, True, f"Launched {Path(executable).name}.")


class CloseApplicationTask:
    task_type = "core.close_application"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        process_name = str(task.config.get("process_name", "")).strip()
        if not process_name or Path(process_name).name != process_name:
            return _result(task, False, "Enter a process name such as obs64.exe.")
        command = ["taskkill", "/IM", process_name]
        if bool(task.config.get("force", False)):
            command.append("/F")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        detail = (completed.stdout or completed.stderr).strip()
        return _result(task, completed.returncode == 0, detail or f"Closed {process_name}.")


class DelayTask:
    task_type = "core.delay"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        seconds = max(0.0, min(float(task.config.get("seconds", 1.0)), 86_400.0))
        loop = QEventLoop()
        QTimer.singleShot(round(seconds * 1000), loop.quit)
        loop.exec()
        return _result(task, True, f"Waited {seconds:g} seconds.")


class WaitForServiceTask:
    task_type = "core.wait_for_service"

    def __init__(self, status: Callable[[str], bool]) -> None:
        self._status = status

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        service = str(task.config.get("service", "obs")).strip().casefold()
        timeout = max(0.1, min(float(task.config.get("timeout_seconds", 15)), 3600.0))
        if self._status(service):
            return _result(task, True, f"{service.upper()} is connected.")
        loop = QEventLoop()
        elapsed = 0
        timer = QTimer()
        timer.setInterval(100)

        def poll() -> None:
            nonlocal elapsed
            elapsed += 100
            if self._status(service) or elapsed >= timeout * 1000:
                loop.quit()

        timer.timeout.connect(poll)
        timer.start()
        loop.exec()
        timer.stop()
        connected = self._status(service)
        return _result(
            task,
            connected,
            f"{service.upper()} is connected." if connected else f"Timed out waiting for {service.upper()}.",
        )


class OpenTargetTask:
    task_type = "core.open_target"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        target = str(task.config.get("target", "")).strip()
        if not target:
            return _result(task, False, "Enter a file, folder, or URL.")
        url = QUrl(target) if target.casefold().startswith(("http://", "https://")) else QUrl.fromLocalFile(str(Path(target).expanduser().resolve()))
        opened = QDesktopServices.openUrl(url)
        return _result(task, opened, f"Opened {target}." if opened else f"Could not open {target}.")


class PythonScriptTask:
    """Run a trusted Python file outside Sally's process."""

    task_type = "core.run_python_script"
    MAX_OUTPUT_LENGTH = 8_000

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            script = self._script_path(task.config, trigger.context)
            command = [
                *self._interpreter_command(task.config),
                str(script),
                *self._arguments(task.config, trigger.context),
            ]
            working_directory = self._working_directory(
                task.config, trigger.context, script
            )
            environment = self._environment(trigger)
            if not bool(task.config.get("wait_for_completion", True)):
                return self._start_background(
                    task, command, working_directory, environment
                )
            return self._run_and_wait(
                task, command, working_directory, environment
            )
        except (OSError, TypeError, ValueError) as error:
            return _result(task, False, str(error))

    @classmethod
    def _script_path(
        cls,
        config: Mapping[str, object],
        context: Mapping[str, str],
    ) -> Path:
        value = cls._render(str(config.get("script", "")), context).strip()
        if not value:
            raise ValueError("Choose a Python script to run.")
        script = Path(value).expanduser().resolve()
        if not script.is_file():
            raise ValueError(f"Python script was not found: {script}")
        if script.suffix.casefold() not in {".py", ".pyw"}:
            raise ValueError("Python automation scripts must use .py or .pyw.")
        return script

    @staticmethod
    def _interpreter_command(config: Mapping[str, object]) -> list[str]:
        configured = str(config.get("python_executable", "")).strip()
        if configured:
            executable = Path(configured).expanduser().resolve()
            if not executable.is_file():
                raise ValueError(f"Python executable was not found: {executable}")
            return [str(executable)]
        if not getattr(sys, "frozen", False):
            return [sys.executable]
        python = shutil.which("python") or shutil.which("python3")
        if python:
            return [python]
        launcher = shutil.which("py")
        if launcher:
            return [launcher, "-3"]
        raise ValueError(
            "Python was not found. Choose a Python executable in this task's settings."
        )

    @classmethod
    def _arguments(
        cls,
        config: Mapping[str, object],
        context: Mapping[str, str],
    ) -> list[str]:
        rendered = cls._render(str(config.get("arguments", "")), context).strip()
        if not rendered:
            return []
        values = shlex.split(rendered, posix=os.name != "nt")
        if os.name == "nt":
            values = [cls._strip_matching_quotes(value) for value in values]
        return values

    @classmethod
    def _working_directory(
        cls,
        config: Mapping[str, object],
        context: Mapping[str, str],
        script: Path,
    ) -> Path:
        value = cls._render(
            str(config.get("working_directory", "")), context
        ).strip()
        directory = Path(value).expanduser().resolve() if value else script.parent
        if not directory.is_dir():
            raise ValueError(f"Working folder was not found: {directory}")
        return directory

    @staticmethod
    def _environment(trigger: TriggerEvent) -> dict[str, str]:
        environment = dict(os.environ)
        context = {str(key): str(value) for key, value in trigger.context.items()}
        environment.update(
            {
                "SALLY_EVENT_ID": trigger.event_id,
                "SALLY_TRIGGER_ID": trigger.trigger_id,
                "SALLY_TRIGGER_SERVICE": trigger.service,
                "SALLY_TRIGGER_TYPE": trigger.trigger_type,
                "SALLY_TRIGGER_CONTEXT": json.dumps(context, ensure_ascii=False),
            }
        )
        for key, value in context.items():
            safe_key = "".join(
                character if character.isalnum() else "_"
                for character in key.upper()
            ).strip("_")
            if safe_key:
                environment[f"SALLY_{safe_key}"] = value
        return environment

    @classmethod
    def _run_and_wait(
        cls,
        task: TaskDefinition,
        command: list[str],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> TaskExecutionResult:
        process = QProcess()
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.setWorkingDirectory(str(working_directory))
        process_environment = QProcessEnvironment()
        for key, value in environment.items():
            process_environment.insert(key, value)
        process.setProcessEnvironment(process_environment)
        capture_output = bool(task.config.get("capture_output", True))
        if capture_output:
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        else:
            process.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)

        process.start()
        if not process.waitForStarted(5_000):
            return _result(
                task,
                False,
                process.errorString() or "Python process could not be started.",
            )

        timeout_seconds = max(
            0.1,
            min(float(task.config.get("timeout_seconds", 30.0)), 86_400.0),
        )
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timed_out = False

        def stop_for_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            process.kill()

        process.finished.connect(loop.quit)
        process.errorOccurred.connect(lambda _error: loop.quit())
        timer.timeout.connect(stop_for_timeout)
        timer.start(round(timeout_seconds * 1000))
        if process.state() is not QProcess.ProcessState.NotRunning:
            loop.exec()
        timer.stop()
        if process.state() is not QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1_000)

        output = ""
        if capture_output:
            output = bytes(process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            ).strip()
        stop_on_failure = bool(task.config.get("stop_on_failure", True))
        if timed_out:
            detail = f"Python script timed out after {timeout_seconds:g} seconds."
            return _result(task, not stop_on_failure, cls._with_output(detail, output))

        exit_code = process.exitCode()
        succeeded = exit_code == 0 or not stop_on_failure
        detail = (
            "Python script completed successfully."
            if exit_code == 0
            else f"Python script exited with code {exit_code}."
        )
        if exit_code != 0 and not stop_on_failure:
            detail += " The routine will continue."
        return _result(task, succeeded, cls._with_output(detail, output))

    @staticmethod
    def _start_background(
        task: TaskDefinition,
        command: list[str],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> TaskExecutionResult:
        process = subprocess.Popen(
            command,
            cwd=str(working_directory),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
        return _result(
            task,
            True,
            f"Started Python script in the background (process {process.pid}).",
        )

    @classmethod
    def _with_output(cls, detail: str, output: str) -> str:
        if not output:
            return detail
        if len(output) > cls.MAX_OUTPUT_LENGTH:
            output = "… output truncated …\n" + output[-cls.MAX_OUTPUT_LENGTH :]
        return f"{detail}\n{output}"

    @staticmethod
    def _render(template: str, context: Mapping[str, str]) -> str:
        from automation.variables import TEMPLATE_PATTERN

        return TEMPLATE_PATTERN.sub(
            lambda match: str(context.get(match.group(1), "")),
            template,
        )

    @staticmethod
    def _strip_matching_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value
