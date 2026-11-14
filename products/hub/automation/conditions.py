from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


CONDITION_JOIN_ALL = "and"
CONDITION_JOIN_ANY = "or"
CONDITION_JOIN_MODES = frozenset({CONDITION_JOIN_ALL, CONDITION_JOIN_ANY})


@dataclass(frozen=True, slots=True)
class ConditionDefinition:
    """One plain-language comparison shared by conditional Automation UI."""

    left: str = ""
    operator: str = "equals"
    right: str = ""
    ignore_case: bool = False
    group_id: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> ConditionDefinition:
        return cls(
            left=str(values.get("left", "")).strip(),
            operator=str(values.get("operator", "equals")).strip().casefold()
            or "equals",
            right=str(values.get("right", "")).strip(),
            ignore_case=bool(values.get("ignore_case", False)),
            group_id=str(values.get("group_id", "")).strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "left": self.left,
            "operator": self.operator,
            "right": self.right,
            "ignore_case": self.ignore_case,
            "group_id": self.group_id,
        }


def condition_definitions(config: Mapping[str, Any]) -> tuple[ConditionDefinition, ...]:
    """Read current conditions or migrate the released single-condition shape."""

    raw = config.get("conditions")
    if isinstance(raw, list):
        values = tuple(
            ConditionDefinition.from_mapping(value)
            for value in raw
            if isinstance(value, Mapping)
        )
        if values:
            return values
    return (
        ConditionDefinition(
            left=str(config.get("left", "")).strip(),
            operator=str(config.get("operator", "equals")).strip().casefold()
            or "equals",
            right=str(config.get("right", "")).strip(),
            ignore_case=bool(config.get("ignore_case", False)),
            group_id=str(config.get("group_id", "")).strip(),
        ),
    )


def condition_join_mode(config: Mapping[str, Any]) -> str:
    value = str(config.get("condition_join", CONDITION_JOIN_ALL)).strip().casefold()
    return value if value in CONDITION_JOIN_MODES else CONDITION_JOIN_ALL


def normalize_if_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one current If config shape.

    Streamhouse Hub 0.3 stored one comparison in top-level ``left``/``operator``
    fields. Loading it through this boundary converts it to the reusable
    condition-list representation while preserving task and child-task IDs.
    """

    normalized = dict(config)
    conditions = condition_definitions(normalized)
    for key in ("left", "operator", "right", "ignore_case", "group_id"):
        normalized.pop(key, None)
    normalized["condition_join"] = condition_join_mode(normalized)
    normalized["conditions"] = [condition.to_dict() for condition in conditions]
    return normalized


def condition_count(config: Mapping[str, Any]) -> int:
    return len(condition_definitions(config))


def primary_condition_subject(config: Mapping[str, Any]) -> str:
    conditions = condition_definitions(config)
    return conditions[0].left if conditions else ""


def condition_dependencies(config: Mapping[str, Any]) -> frozenset[str]:
    """Return canonical Variables referenced by one flat condition set."""

    from products.hub.automation.variable_registry import placeholder_names

    names: set[str] = set()
    for condition in condition_definitions(config):
        names.update(placeholder_names(condition.left))
        names.update(placeholder_names(condition.right))
    return frozenset(names)


def evaluate_condition_set(
    config: Mapping[str, Any],
    context: dict[str, str],
    *,
    user_groups=None,
) -> bool:
    """Evaluate the shared If condition contract for tasks or triggers."""

    # Imported lazily because logic_tasks owns the established comparison
    # implementation while also consuming the condition data model.
    from products.hub.automation.logic_tasks import evaluate_if_condition

    results: list[bool] = []
    for condition in condition_definitions(config):
        if condition.operator == "user_in_group":
            if (
                user_groups is None
                or user_groups.get_group(condition.group_id) is None
            ):
                raise ValueError("The selected user group no longer exists.")
            result = user_groups.is_member(
                str(context.get("user.id", "")).strip(),
                condition.group_id,
            )
        else:
            result = evaluate_if_condition(
                condition.left,
                condition.operator,
                condition.right,
                context,
                ignore_case=condition.ignore_case,
            )
        results.append(result)
    return (
        all(results)
        if condition_join_mode(config) == CONDITION_JOIN_ALL
        else any(results)
    )
