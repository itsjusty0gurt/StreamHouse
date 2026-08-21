from __future__ import annotations

from collections.abc import Mapping

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.variable_registry import (
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
)


INTEGER_SUFFIXES = frozenset(
    {
        "amount_changed",
        "channel_total",
        "stream_total",
        "viewer_total",
        "viewer_stream_total",
        "viewer_rank",
        "leaderboard_entries",
        "top_viewer_value",
        "file_count",
        "line_count",
    }
)
BOOLEAN_SUFFIXES = frozenset({"accepted", "available", "exists"})


def generated_output_definitions(
    task_type: str,
    config: Mapping[str, object],
    *,
    source: str = "",
) -> tuple[VariableDefinition, ...]:
    """Describe routine-scoped task outputs without registering them globally."""
    names = CustomVariableStore.generated_names(task_type, config)
    task_source = source.strip() or _source_name(task_type)
    definitions: list[VariableDefinition] = []
    for name in names:
        data_type = _output_type(task_type, name, config)
        display_name = _display_name(name, config)
        availability = _output_availability(task_type)
        lifetime_text = (
            "while its variable store scope remains active"
            if availability is VariableAvailability.GLOBAL
            else "during this routine"
        )
        definitions.append(
            VariableDefinition(
                name=name,
                display_name=display_name,
                description=(
                    f"{display_name} produced by {task_source} {lifetime_text}."
                ),
                data_type=data_type,
                source=task_source,
                category="Automation outputs",
                availability=availability,
                writable=False,
                preview_value=_preview_value(data_type),
                legacy=True,
            )
        )
    return tuple(definitions)


def _output_availability(task_type: str) -> VariableAvailability:
    if task_type.strip().casefold() in {
        "core.create_global_variable",
        "core.create_session_variable",
    }:
        return VariableAvailability.GLOBAL
    return VariableAvailability.TEMPORARY


def _source_name(task_type: str) -> str:
    return str(task_type).replace(".", " — ", 1).replace("_", " ").title()


def _output_type(
    task_type: str,
    name: str,
    config: Mapping[str, object],
) -> VariableDataType:
    normalized = task_type.strip().casefold()
    if normalized == "core.logic_random_number":
        return (
            VariableDataType.NUMBER
            if str(config.get("mode", "integer")).casefold() == "decimal"
            else VariableDataType.INTEGER
        )
    if normalized == "core.path_exists":
        return VariableDataType.BOOLEAN
    if normalized == "core.file_count_lines":
        return VariableDataType.INTEGER
    suffix = _matching_suffix(name)
    if suffix in INTEGER_SUFFIXES:
        return VariableDataType.INTEGER
    if suffix in BOOLEAN_SUFFIXES:
        return VariableDataType.BOOLEAN
    return VariableDataType.TEXT


def _matching_suffix(name: str) -> str:
    candidates = sorted(
        (*INTEGER_SUFFIXES, *BOOLEAN_SUFFIXES), key=len, reverse=True
    )
    return next(
        (
            suffix
            for suffix in candidates
            if name == suffix or name.endswith(f"_{suffix}")
        ),
        "",
    )


def _display_name(name: str, config: Mapping[str, object]) -> str:
    prefix = str(
        config.get("output_prefix") or config.get("counter_id") or ""
    ).strip().casefold()
    suffix = (
        name.removeprefix(f"{prefix}_")
        if prefix and name.startswith(f"{prefix}_")
        else name
    )
    label = suffix.replace("_", " ").title()
    return f"{prefix.replace('_', ' ').title()} — {label}" if prefix else label


def _preview_value(data_type: VariableDataType) -> object:
    if data_type is VariableDataType.BOOLEAN:
        return True
    if data_type in {VariableDataType.INTEGER, VariableDataType.NUMBER}:
        return 1
    return "Example"
