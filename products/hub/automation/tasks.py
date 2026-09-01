from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent


class TaskHandler(Protocol):
    task_type: str

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult: ...


@dataclass(frozen=True, slots=True)
class TaskInputHelp:
    """Plain-language guidance for one task configuration field."""

    key: str
    description: str


TaskReferenceResolver = Callable[[str, str], str]
TaskCardSummaryFormatter = Callable[
    [Mapping[str, Any], TaskReferenceResolver | None],
    str,
]


@dataclass(frozen=True, slots=True)
class TaskMetadata:
    """User-facing reference metadata for one registered task type."""

    task_type: str
    label: str
    short_description: str
    category: str
    visible: bool = True
    help_text: str = ""
    input_help: tuple[TaskInputHelp, ...] = ()
    variable_inputs: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    card_summary_formatter: TaskCardSummaryFormatter | None = None
    card_summary_required: bool = True

    def input_description(self, key: str) -> str:
        return next(
            (item.description for item in self.input_help if item.key == key),
            "",
        )

    def search_text(self) -> str:
        return " ".join(
            (
                self.label,
                self.short_description,
                self.category,
                self.task_type,
                self.help_text,
                *(item.description for item in self.input_help),
                *self.requirements,
                *self.notes,
                *self.examples,
            )
        )

    def format_card_summary(
        self,
        config: Mapping[str, Any],
        resolver: TaskReferenceResolver | None = None,
    ) -> str:
        if self.card_summary_formatter is None:
            return ""
        return self.card_summary_formatter(config, resolver).strip()


class TaskRegistry:
    def __init__(self, metadata: Iterable[TaskMetadata] = ()) -> None:
        self._handlers: dict[str, TaskHandler] = {}
        self._metadata: dict[str, TaskMetadata] = {}
        for definition in metadata:
            self.register_metadata(definition)

    @staticmethod
    def _task_type(value: object) -> str:
        clean_type = str(value).strip().casefold()
        if not clean_type:
            raise ValueError("Task types cannot be blank.")
        return clean_type

    def register_metadata(self, metadata: TaskMetadata) -> None:
        clean_type = self._task_type(metadata.task_type)
        if clean_type in self._metadata:
            raise ValueError(f"Task metadata is already registered for {clean_type}.")
        if not metadata.label.strip():
            raise ValueError("Task metadata requires a user-facing label.")
        if not metadata.category.strip():
            raise ValueError("Task metadata requires a category.")
        if metadata.visible and not metadata.short_description.strip():
            raise ValueError(
                f"Visible task metadata for {clean_type} requires a short description."
            )
        if metadata.visible and not metadata.help_text.strip():
            raise ValueError(
                f"Visible task metadata for {clean_type} requires detailed help."
            )
        if (
            metadata.visible
            and metadata.card_summary_required
            and metadata.card_summary_formatter is None
        ):
            raise ValueError(
                f"Visible task metadata for {clean_type} requires a card summary."
            )
        self._metadata[clean_type] = metadata

    def register(self, handler: TaskHandler) -> None:
        clean_type = self._task_type(handler.task_type)
        self._handlers[clean_type] = handler

    def unregister(self, task_type: str) -> bool:
        return self._handlers.pop(task_type.strip().casefold(), None) is not None

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def metadata(self, task_type: str) -> TaskMetadata | None:
        return self._metadata.get(str(task_type).strip().casefold())

    def visible_metadata(self) -> tuple[TaskMetadata, ...]:
        return tuple(
            sorted(
                (definition for definition in self._metadata.values() if definition.visible),
                key=lambda definition: (
                    definition.category.casefold(),
                    definition.label.casefold(),
                    definition.task_type,
                ),
            )
        )

    def missing_descriptions(self) -> tuple[str, ...]:
        return tuple(
            definition.task_type
            for definition in self.visible_metadata()
            if not definition.short_description.strip()
        )

    def missing_help(self) -> tuple[str, ...]:
        return tuple(
            definition.task_type
            for definition in self.visible_metadata()
            if not definition.help_text.strip()
        )

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        handler = self._handlers.get(task.task_type.strip().casefold())
        if handler is None:
            return TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail=f"No task provider is registered for {task.task_type}.",
            )
        return handler.execute(task, trigger)
