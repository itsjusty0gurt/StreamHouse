from __future__ import annotations

import re
from collections.abc import Mapping

from products.hub.automation.variable_registry import (
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
    validate_variable_name,
)


OUTPUT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

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
        "stream_viewers",
    }
)
BOOLEAN_SUFFIXES = frozenset({"accepted", "available", "exists", "is_live", "is_following"})
DATETIME_SUFFIXES = frozenset({"created_at", "started_at", "followed_at"})


def output_id(value: object) -> str:
    clean = str(value).strip().casefold().removeprefix("automation.")
    if not OUTPUT_ID_PATTERN.fullmatch(clean):
        raise ValueError(
            "Automation output names must start with a letter and contain only "
            "lowercase letters, numbers, and underscores."
        )
    return clean


def automation_output_name(value: object, suffix: str = "") -> str:
    base = output_id(value)
    if suffix:
        clean_suffix = output_id(suffix)
        base = f"{base}_{clean_suffix}"
    return validate_variable_name(f"automation.{base}")


def generated_output_definitions(
    task_type: str,
    config: Mapping[str, object],
    *,
    source: str = "",
) -> tuple[VariableDefinition, ...]:
    """Describe routine-scoped task outputs without registering them globally."""
    normalized = task_type.strip().casefold()
    try:
        names = _output_definition_names(normalized, config)
    except ValueError:
        return ()
    task_source = source.strip() or _source_name(task_type)
    definitions: list[VariableDefinition] = []
    for name in names:
        data_type = _output_type(task_type, name, config)
        display_name = _display_name(name)
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
                writable=availability is VariableAvailability.GLOBAL,
                preview_value=_preview_value(data_type),
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


STATIC_OUTPUTS = {
    "twitch.resolve_user": ("target_user_id", "target_login", "target_display_name", "account_created_at", "user_lookup_status"),
    "twitch.get_stream_information": ("stream_status", "is_live", "stream_started_at", "stream_title", "stream_category", "stream_id", "stream_viewers", "stream_game_id"),
    "twitch.get_channel_information": ("channel_info_status", "title_status", "category_status", "stream_title", "stream_category", "stream_game_id"),
    "twitch.get_follow_relationship": ("is_following", "followed_at", "follow_status", "channel_display_name"),
    "twitch.build_command_list": ("command_list", "command_list_status"),
}


def _output_definition_names(task_type: str, config: Mapping[str, object]) -> tuple[str, ...]:
    if task_type.startswith("counter."):
        from products.hub.counters.tasks import OUTPUT_SUFFIXES

        prefix = output_id(config.get("output_prefix") or config.get("counter_id", ""))
        return tuple(automation_output_name(prefix, suffix) for suffix in OUTPUT_SUFFIXES)
    if task_type in STATIC_OUTPUTS:
        return tuple(automation_output_name(name) for name in STATIC_OUTPUTS[task_type])
    if task_type in {"twitch.get_channel_information_field", "twitch.build_social_links_message"}:
        default = str(config.get("field", "")) if task_type.endswith("_field") else "social_links_message"
        base = output_id(config.get("output_variable") or default)
        return tuple(
            automation_output_name(name)
            for name in (base, f"{base}_status", "channel_information_available", "channel_information_status")
        )
    key = {
        "core.create_global_variable": "name",
        "core.create_session_variable": "name",
        "core.create_routine_variable": "name",
        "core.logic_get_input": "name",
        "core.logic_random_number": "name",
        "core.file_read": "variable",
        "core.file_random_line": "variable",
        "core.file_specific_line": "variable",
        "core.path_exists": "variable",
        "core.file_count_lines": "variable",
        "core.format_duration": "output_variable",
        "core.select_text": "output_variable",
    }.get(task_type)
    if key is None:
        return ()
    base = output_id(config.get(key, ""))
    if task_type in {"core.create_global_variable", "core.create_session_variable"}:
        return (validate_variable_name(f"custom.{base}"),)
    names = [automation_output_name(base)]
    if task_type == "core.logic_get_input":
        names.append(automation_output_name(base, "accepted"))
    if task_type == "core.format_duration":
        names.append(automation_output_name(base, "status"))
    return tuple(names)


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
    if suffix in DATETIME_SUFFIXES:
        return VariableDataType.DATETIME
    return VariableDataType.TEXT


def _matching_suffix(name: str) -> str:
    leaf = name.split(".", 1)[-1]
    candidates = sorted(
        (*INTEGER_SUFFIXES, *BOOLEAN_SUFFIXES, *DATETIME_SUFFIXES), key=len, reverse=True
    )
    return next(
        (
            suffix
            for suffix in candidates
            if leaf == suffix or leaf.endswith(f"_{suffix}")
        ),
        "",
    )


def _display_name(name: str) -> str:
    return name.split(".", 1)[1].replace("_", " ").title()


def _preview_value(data_type: VariableDataType) -> object:
    if data_type is VariableDataType.BOOLEAN:
        return True
    if data_type in {VariableDataType.INTEGER, VariableDataType.NUMBER}:
        return 1
    if data_type is VariableDataType.DATETIME:
        return "2026-01-01T00:00:00Z"
    return "Example"
