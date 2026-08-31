from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, ClassVar, Mapping
from uuid import uuid4


DEFAULT_AUTOMATION_QUEUE_ID = "streamhouse.default.queue"
DEFAULT_AUTOMATION_QUEUE_NAME = "Default Queue"


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    SEGMENT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^[a-z0-9][a-z0-9_.-]*$"
    )
    trigger_id: str
    service: str
    trigger_type: str
    context: Mapping[str, str]
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        service = self.service.strip().casefold()
        trigger_type = self.trigger_type.strip().casefold()
        if not self.trigger_id.strip():
            raise ValueError("Trigger events require a trigger ID.")
        if not self.SEGMENT_PATTERN.fullmatch(service):
            raise ValueError("Trigger service names use lowercase event segments.")
        if not self.SEGMENT_PATTERN.fullmatch(trigger_type):
            raise ValueError("Trigger type names use lowercase event segments.")
        object.__setattr__(self, "service", service)
        object.__setattr__(self, "trigger_type", trigger_type)


@dataclass(slots=True)
class TaskDefinition:
    task_id: str
    task_type: str
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    managed_key: str = ""

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> TaskDefinition:
        config = values.get("config", {})
        return cls(
            task_id=str(values.get("task_id", "")) or uuid4().hex,
            task_type=str(values.get("task_type", "")),
            name=str(values.get("name", "")),
            config=dict(config) if isinstance(config, dict) else {},
            enabled=bool(values.get("enabled", True)),
            managed_key=str(values.get("managed_key", "")),
        )


@dataclass(slots=True)
class RoutineGroup:
    group_id: str
    name: str
    collapsed: bool = False

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> RoutineGroup:
        return cls(
            group_id=str(values.get("group_id", "")) or uuid4().hex,
            name=str(values.get("name", "")),
            collapsed=bool(values.get("collapsed", False)),
        )


@dataclass(slots=True)
class RoutineDefinition:
    routine_id: str
    name: str
    trigger_id: str
    tasks: list[TaskDefinition] = field(default_factory=list)
    enabled: bool = True
    managed_by: str = ""
    group_id: str = ""
    description: str = ""
    additional_trigger_ids: list[str] = field(default_factory=list)
    queue_id: str = DEFAULT_AUTOMATION_QUEUE_ID

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> RoutineDefinition:
        raw_tasks = values.get("tasks", [])
        return cls(
            routine_id=str(values.get("routine_id", "")) or uuid4().hex,
            name=str(values.get("name", "")),
            trigger_id=str(values.get("trigger_id", "")),
            tasks=[
                TaskDefinition.from_dict(task)
                for task in raw_tasks
                if isinstance(task, dict)
            ]
            if isinstance(raw_tasks, list)
            else [],
            enabled=bool(values.get("enabled", True)),
            managed_by=str(values.get("managed_by", "")),
            group_id=str(values.get("group_id", "")),
            description=str(values.get("description", "")),
            additional_trigger_ids=[
                str(value)
                for value in values.get("additional_trigger_ids", [])
                if str(value).strip()
            ],
            queue_id=(
                str(values.get("queue_id", "")).strip()
                or DEFAULT_AUTOMATION_QUEUE_ID
            ),
        )

    @property
    def trigger_ids(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (self.trigger_id, *self.additional_trigger_ids)
            if value
        )


@dataclass(frozen=True, slots=True)
class TaskExecutionResult:
    task_id: str
    task_type: str
    succeeded: bool
    detail: str = ""
    duration_ms: int = 0
    flow_action: str = ""
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class RoutineExecutionResult:
    routine_id: str
    succeeded: bool
    task_results: tuple[TaskExecutionResult, ...] = ()
    detail: str = ""
    flow_action: str = ""
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class AutomationExecutionResult:
    event_id: str
    trigger_id: str
    routine_results: tuple[RoutineExecutionResult, ...] = ()

    @property
    def handled(self) -> bool:
        return bool(self.routine_results)

    @property
    def succeeded(self) -> bool:
        return self.handled and all(
            result.succeeded for result in self.routine_results
        )
