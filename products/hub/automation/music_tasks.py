from __future__ import annotations

from dataclasses import dataclass

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.integrations.music_player import MusicPlayerService


MUSIC_TASK_LABELS = {
    "music.play": "Music — Play",
    "music.pause": "Music — Pause",
    "music.play_pause": "Music — Play/Pause",
    "music.next": "Music — Next Track",
    "music.previous": "Music — Previous Track",
    "music.set_volume": "Music — Set Volume",
    "music.set_muted": "Music — Set Muted",
}


@dataclass(slots=True)
class MusicCommandTask:
    task_type: str
    command: str
    service: MusicPlayerService
    variable_registry: VariableRegistry

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        try:
            arguments = self._arguments(task, trigger)
        except ValueError as error:
            return TaskExecutionResult(
                task.task_id,
                task.task_type,
                False,
                str(error),
            )
        result = self.service.send_command(
            self.command,
            arguments,
            timeout_ms=5_000,
        )
        return TaskExecutionResult(
            task.task_id,
            task.task_type,
            result.succeeded,
            result.detail,
        )

    def _arguments(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> dict[str, object]:
        if self.command == "set_volume":
            template = str(task.config.get("volume", "100")).strip()
            rendered = self.variable_registry.render(template, trigger.context).strip()
            if "{" in rendered or "}" in rendered:
                raise ValueError("Music volume uses an unavailable Variable.")
            try:
                numeric = float(rendered)
            except ValueError as error:
                raise ValueError("Music volume must be a number from 0 to 100.") from error
            if not numeric.is_integer() or not 0 <= numeric <= 100:
                raise ValueError("Music volume must be a whole number from 0 to 100.")
            return {"volume": int(numeric)}
        if self.command == "set_muted":
            muted = task.config.get("muted", True)
            if type(muted) is not bool:
                raise ValueError("Choose Yes or No for Music muted state.")
            return {"muted": muted}
        return {}


def register_music_tasks(
    registry: TaskRegistry,
    service: MusicPlayerService,
    variable_registry: VariableRegistry,
) -> None:
    for task_type, command in (
        ("music.play", "play"),
        ("music.pause", "pause"),
        ("music.play_pause", "play_pause"),
        ("music.next", "next"),
        ("music.previous", "previous"),
        ("music.set_volume", "set_volume"),
        ("music.set_muted", "set_muted"),
    ):
        registry.register(
            MusicCommandTask(task_type, command, service, variable_registry)
        )
