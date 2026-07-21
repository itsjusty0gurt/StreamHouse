from __future__ import annotations

import random
from pathlib import Path
from typing import Mapping, MutableMapping

from automation.custom_variables import CustomVariableStore
from automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from automation.tasks import TaskRegistry
from automation.variables import render_preview


FILE_TASK_LABELS = {
    "core.file_read": "Core — Read text file",
    "core.file_random_line": "Core — Read random line",
    "core.file_specific_line": "Core — Read specific line",
    "core.file_write": "Core — Write text file",
    "core.path_exists": "Core — Check file or folder",
    "core.file_count_lines": "Core — Count file lines",
}
FILE_TASK_TYPES = frozenset(FILE_TASK_LABELS)
MAX_READ_BYTES = 10 * 1024 * 1024


def _result(
    task: TaskDefinition,
    succeeded: bool,
    detail: str,
) -> TaskExecutionResult:
    return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)


def _context(trigger: TriggerEvent) -> MutableMapping[str, str]:
    if not isinstance(trigger.context, MutableMapping):
        raise ValueError("This automation execution does not have a writable context.")
    return trigger.context


def _path(config: Mapping[str, object], context: Mapping[str, str]) -> Path:
    rendered = render_preview(str(config.get("path", "")), context).strip()
    if not rendered:
        raise ValueError("Choose a file or folder path.")
    return Path(rendered).expanduser().resolve()


def _variable(config: Mapping[str, object]) -> str:
    return CustomVariableStore.validate_name(str(config.get("variable", "")))


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"Text file was not found: {path}")
    if path.stat().st_size > MAX_READ_BYTES:
        raise ValueError("Text files used by automation must be 10 MB or smaller.")
    return path.read_text(encoding="utf-8-sig")


def _lines(text: str, ignore_blank: bool) -> list[str]:
    lines = text.splitlines()
    if ignore_blank:
        lines = [line for line in lines if line.strip()]
    return lines


def _failure(task: TaskDefinition, error: Exception) -> TaskExecutionResult:
    stop = bool(task.config.get("stop_on_failure", True))
    detail = str(error)
    if not stop:
        detail += " The routine will continue."
    return _result(task, stop is False, detail)


class ReadTextFileTask:
    task_type = "core.file_read"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            path = _path(task.config, context)
            variable = _variable(task.config)
            value = _read_text(path)
            if bool(task.config.get("trim", False)):
                value = value.strip()
            context[variable] = value
            return _result(task, True, f'Read {len(value)} character(s) into "{variable}".')
        except (OSError, TypeError, ValueError, UnicodeError) as error:
            return _failure(task, error)


class ReadRandomLineTask:
    task_type = "core.file_random_line"

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            path = _path(task.config, context)
            variable = _variable(task.config)
            lines = _lines(
                _read_text(path),
                bool(task.config.get("ignore_blank_lines", True)),
            )
            if not lines:
                raise ValueError("The text file has no eligible lines.")
            value = self.rng.choice(lines)
            context[variable] = value
            return _result(task, True, f'Read a random line into "{variable}".')
        except (OSError, TypeError, ValueError, UnicodeError) as error:
            return _failure(task, error)


class ReadSpecificLineTask:
    task_type = "core.file_specific_line"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            path = _path(task.config, context)
            variable = _variable(task.config)
            rendered_line = render_preview(
                str(task.config.get("line_number", "1")), context
            ).strip()
            line_number = int(rendered_line)
            if line_number < 1:
                raise ValueError("Line numbers start at 1.")
            lines = _lines(
                _read_text(path),
                bool(task.config.get("ignore_blank_lines", False)),
            )
            if line_number > len(lines):
                raise ValueError(
                    f"Line {line_number} does not exist; the file has {len(lines)} line(s)."
                )
            context[variable] = lines[line_number - 1]
            return _result(task, True, f'Read line {line_number} into "{variable}".')
        except (OSError, TypeError, ValueError, UnicodeError) as error:
            return _failure(task, error)


class WriteTextFileTask:
    task_type = "core.file_write"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            path = _path(task.config, context)
            mode = str(task.config.get("mode", "append")).strip().casefold()
            if mode not in {"append", "overwrite"}:
                raise ValueError("Choose append or overwrite mode.")
            if bool(task.config.get("create_folders", False)):
                path.parent.mkdir(parents=True, exist_ok=True)
            if not path.parent.is_dir():
                raise ValueError(f"Parent folder was not found: {path.parent}")
            text = render_preview(str(task.config.get("text", "")), context)
            if bool(task.config.get("add_newline", True)):
                text += "\n"
            with path.open("a" if mode == "append" else "w", encoding="utf-8") as output:
                output.write(text)
            action = "Appended to" if mode == "append" else "Wrote"
            return _result(task, True, f"{action} {path.name}.")
        except (OSError, TypeError, ValueError, UnicodeError) as error:
            return _failure(task, error)


class PathExistsTask:
    task_type = "core.path_exists"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            path = _path(task.config, context)
            variable = _variable(task.config)
            path_type = str(task.config.get("path_type", "either")).strip().casefold()
            checks = {
                "file": path.is_file,
                "folder": path.is_dir,
                "either": path.exists,
            }
            if path_type not in checks:
                raise ValueError("Choose file, folder, or either path type.")
            exists = checks[path_type]()
            context[variable] = "true" if exists else "false"
            return _result(
                task,
                True,
                f'Path check stored {str(exists).lower()} in "{variable}".',
            )
        except (OSError, TypeError, ValueError) as error:
            return _failure(task, error)


class CountFileLinesTask:
    task_type = "core.file_count_lines"

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            context = _context(trigger)
            path = _path(task.config, context)
            variable = _variable(task.config)
            count = len(
                _lines(
                    _read_text(path),
                    bool(task.config.get("ignore_blank_lines", False)),
                )
            )
            context[variable] = str(count)
            return _result(task, True, f'Stored {count} line(s) in "{variable}".')
        except (OSError, TypeError, ValueError, UnicodeError) as error:
            return _failure(task, error)


def register_file_tasks(registry: TaskRegistry) -> None:
    registry.register(ReadTextFileTask())
    registry.register(ReadRandomLineTask())
    registry.register(ReadSpecificLineTask())
    registry.register(WriteTextFileTask())
    registry.register(PathExistsTask())
    registry.register(CountFileLinesTask())
