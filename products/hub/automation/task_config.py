from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from products.hub.counters.models import SCOPES


COUNTER_TASK_TYPES = frozenset(
    {
        "counter.increase",
        "counter.decrease",
        "counter.set_value",
        "counter.reset",
    }
)

COUNTER_SCOPE_OPTIONS = (
    ("User", "viewer_total"),
    ("User per stream", "viewer_stream_total"),
    ("Per stream", "stream_total"),
    ("Lifetime", "channel_total"),
)


def normalize_task_config(
    task_type: str,
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the current config shape for a task.

    Counter tasks shipped with one ``scope`` string. Their current shape is a
    ``scopes`` list; normalizing at load/edit/store boundaries keeps execution
    on one representation while preserving the old task's exact selection.
    """

    normalized = deepcopy(dict(config or {}))
    if str(task_type).strip().casefold() not in COUNTER_TASK_TYPES:
        return normalized

    raw_scopes = normalized.get("scopes")
    if isinstance(raw_scopes, (list, tuple)):
        candidates = raw_scopes
    elif raw_scopes is None:
        candidates = (normalized.get("scope", "channel_total"),)
    else:
        candidates = (raw_scopes,)

    selected: list[str] = []
    for value in candidates:
        scope = str(value).strip().casefold()
        if scope and scope not in selected:
            selected.append(scope)
    normalized.pop("scope", None)
    normalized["scopes"] = selected
    return normalized


def selected_counter_scopes(config: Mapping[str, Any]) -> tuple[str, ...]:
    raw_scopes = config.get("scopes", [])
    if not isinstance(raw_scopes, (list, tuple)):
        raise ValueError("Counter task scopes must be a list.")
    selected = tuple(
        dict.fromkeys(
            str(value).strip().casefold()
            for value in raw_scopes
            if str(value).strip()
        )
    )
    if not selected:
        raise ValueError("Select at least one counter scope.")
    unknown = tuple(scope for scope in selected if scope not in SCOPES)
    if unknown:
        raise ValueError(f'Unknown counter scope: "{unknown[0]}".')
    return selected
