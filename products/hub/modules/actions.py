from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from products.hub.modules.contracts import Availability, validate_stable_id


@dataclass(frozen=True, slots=True)
class ActionParameter:
    parameter_id: str
    label: str
    value_kind: str
    required: bool = True
    variable_aware: bool = False

    def __post_init__(self) -> None:
        clean = str(self.parameter_id).strip().casefold()
        if not clean or not clean.replace("_", "").isalnum():
            raise ValueError("Action parameter IDs must contain letters, numbers, or underscores.")
        object.__setattr__(self, "parameter_id", clean)
        if not self.label.strip() or not self.value_kind.strip():
            raise ValueError("Action parameter labels and value kinds cannot be blank.")


@dataclass(frozen=True, slots=True)
class ActionExecutionContext:
    values: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True, slots=True)
class ActionResult:
    succeeded: bool
    detail: str = ""
    outputs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))


ActionHandler = Callable[[Mapping[str, object], ActionExecutionContext], ActionResult]
AvailabilityProbe = Callable[[], Availability]


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    action_id: str
    display_name: str
    owner_module_id: str
    handler: ActionHandler = field(repr=False, compare=False)
    category: str = "Integration"
    capability: str = ""
    parameters: tuple[ActionParameter, ...] = ()
    availability: AvailabilityProbe | None = field(default=None, repr=False, compare=False)
    permissions: frozenset[str] = frozenset()
    exposure: frozenset[str] = frozenset({"automation"})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            validate_stable_id(self.action_id, label="Action ID"),
        )
        object.__setattr__(
            self,
            "owner_module_id",
            validate_stable_id(self.owner_module_id, label="Owner module ID"),
        )
        if not self.display_name.strip() or not self.category.strip():
            raise ValueError("Action display name and category cannot be blank.")
        parameter_ids = [parameter.parameter_id for parameter in self.parameters]
        if len(parameter_ids) != len(set(parameter_ids)):
            raise ValueError(f'Duplicate parameter ID in action "{self.action_id}".')


class ActionRegistry:
    """Core-owned catalog and invocation boundary for module actions."""

    def __init__(self) -> None:
        self._actions: dict[str, ActionDescriptor] = {}

    def register(self, descriptor: ActionDescriptor) -> None:
        if descriptor.action_id in self._actions:
            raise ValueError(f'Action "{descriptor.action_id}" is already registered.')
        self._actions[descriptor.action_id] = descriptor

    def descriptor(self, action_id: str) -> ActionDescriptor | None:
        return self._actions.get(str(action_id).strip().casefold())

    get = descriptor

    def descriptors(self) -> tuple[ActionDescriptor, ...]:
        return tuple(self._actions[key] for key in sorted(self._actions))

    def invoke(
        self,
        action_id: str,
        parameters: Mapping[str, Any],
        context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        descriptor = self.descriptor(action_id)
        if descriptor is None:
            return ActionResult(False, f'Action "{action_id}" is not registered.')
        if descriptor.availability is not None:
            availability = descriptor.availability()
            if not availability.available:
                return ActionResult(False, availability.detail or "Action is unavailable.")

        supplied = {str(key).strip().casefold(): value for key, value in parameters.items()}
        expected = {parameter.parameter_id: parameter for parameter in descriptor.parameters}
        unknown = sorted(set(supplied) - set(expected))
        if unknown:
            return ActionResult(False, f"Unknown action parameter: {unknown[0]}.")
        missing = [
            parameter.label
            for parameter in descriptor.parameters
            if parameter.required and parameter.parameter_id not in supplied
        ]
        if missing:
            return ActionResult(False, f"Required action parameter is missing: {missing[0]}.")

        result = descriptor.handler(
            MappingProxyType(supplied),
            context or ActionExecutionContext(),
        )
        if not isinstance(result, ActionResult):
            raise TypeError(f'Action "{descriptor.action_id}" returned an invalid result.')
        return result
