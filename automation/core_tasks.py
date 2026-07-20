from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent


CORE_TASK_LABELS = {
    "core.launch_application": "Core — Launch application",
    "core.close_application": "Core — Close application",
    "core.delay": "Core — Wait / delay",
    "core.wait_for_service": "Core — Wait for service",
    "core.open_target": "Core — Open file, folder, or URL",
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
