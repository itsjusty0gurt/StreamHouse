from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from shared.streamhouse_runtime.logger import Logger


VARIABLE_NAME_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]{0,63}(?:\.[a-z][a-z0-9_]{0,63})+$"
)
PLACEHOLDER_PATTERN = re.compile(
    r"\{([a-z][a-z0-9_]{0,63}(?:\.[a-z][a-z0-9_]{0,63})*)\}"
)


class VariableDataType(StrEnum):
    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


class VariableAvailability(StrEnum):
    GLOBAL = "global"
    CONTEXTUAL = "contextual"


@dataclass(frozen=True, slots=True)
class VariableDefinition:
    name: str
    display_name: str
    description: str
    data_type: VariableDataType
    source: str
    category: str
    availability: VariableAvailability = VariableAvailability.GLOBAL
    writable: bool = False
    default: Any | None = None

    def __post_init__(self) -> None:
        name = self.name.strip().casefold()
        if not VARIABLE_NAME_PATTERN.fullmatch(name):
            raise ValueError(f'Invalid canonical variable name: "{self.name}".')
        object.__setattr__(self, "name", name)

    @property
    def placeholder(self) -> str:
        return f"{{{self.name}}}"


@dataclass(frozen=True, slots=True)
class VariableSnapshot:
    definition: VariableDefinition
    value: Any | None = None
    available: bool = False
    detail: str = ""

    @property
    def display_value(self) -> str:
        if not self.available:
            return "Unavailable"
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        return "" if self.value is None else str(self.value)


class VariableProvider(Protocol):
    source: str

    def definitions(self) -> tuple[VariableDefinition, ...]: ...
    def resolve(self, name: str, context: Mapping[str, object]) -> VariableSnapshot: ...
    def set_value(self, name: str, value: object) -> VariableSnapshot: ...


class CallbackVariableProvider:
    """Small provider adapter that keeps domain lookups outside the registry."""

    def __init__(
        self,
        source: str,
        definitions: tuple[VariableDefinition, ...],
        resolver: Callable[[str, Mapping[str, object]], tuple[bool, object, str]],
        writer: Callable[[str, object], object] | None = None,
    ) -> None:
        self.source = source
        self._definitions = definitions
        self._resolver = resolver
        self._writer = writer

    def definitions(self) -> tuple[VariableDefinition, ...]:
        return self._definitions

    def resolve(self, name: str, context: Mapping[str, object]) -> VariableSnapshot:
        definition = next(item for item in self._definitions if item.name == name)
        available, value, detail = self._resolver(name, context)
        if not available and definition.default is not None:
            return VariableSnapshot(definition, definition.default, True, detail)
        return VariableSnapshot(definition, value, available, detail)

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        definition = next(item for item in self._definitions if item.name == name)
        if not definition.writable or self._writer is None:
            raise PermissionError(f'Variable "{name}" is read-only.')
        self._writer(name, value)
        return self.resolve(name, {})


class VariableRegistry:
    """Central metadata, resolution, rendering, and write-routing service."""

    def __init__(self) -> None:
        self._providers: list[VariableProvider] = []

    def register(self, provider: VariableProvider) -> None:
        existing = {item.name for item in self.definitions()}
        offered = provider.definitions()
        names = [item.name for item in offered]
        duplicate = next(
            (name for name in names if name in existing or names.count(name) > 1),
            "",
        )
        if duplicate:
            raise ValueError(f'Variable "{duplicate}" is already registered.')
        self._providers.append(provider)

    def definitions(self) -> tuple[VariableDefinition, ...]:
        return tuple(
            definition
            for provider in self._providers
            for definition in provider.definitions()
        )

    def resolve(
        self, name: str, context: Mapping[str, object] | None = None
    ) -> VariableSnapshot | None:
        clean = name.strip().casefold().removeprefix("{").removesuffix("}")
        for provider in self._providers:
            if clean in {item.name for item in provider.definitions()}:
                return provider.resolve(clean, context or {})
        return None

    def snapshots(
        self, context: Mapping[str, object] | None = None
    ) -> tuple[VariableSnapshot, ...]:
        return tuple(
            provider.resolve(definition.name, context or {})
            for provider in self._providers
            for definition in provider.definitions()
        )

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        snapshot = self.resolve(name)
        if snapshot is None:
            raise KeyError(f'Variable "{name}" is not registered.')
        if not snapshot.definition.writable:
            raise PermissionError(f'Variable "{snapshot.definition.name}" is read-only.')
        for provider in self._providers:
            if snapshot.definition.name in {item.name for item in provider.definitions()}:
                return provider.set_value(snapshot.definition.name, value)
        raise KeyError(f'Variable "{name}" is not registered.')

    def context_values(
        self, context: Mapping[str, object] | None = None
    ) -> dict[str, str]:
        return {
            item.definition.name: item.display_value
            for item in self.snapshots(context)
            if item.available
        }

    def render(
        self,
        template: str,
        context: Mapping[str, object] | None = None,
        *,
        fallback: str | None = None,
    ) -> str:
        supplied = context or {}

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in supplied:
                return self.display_value(supplied[name])
            snapshot = self.resolve(name, supplied)
            if snapshot is not None and snapshot.available:
                return snapshot.display_value
            Logger.debug(
                f'Variable "{name}" was unavailable while rendering text.',
                source="VARIABLES",
            )
            return match.group(0) if fallback is None else fallback

        return PLACEHOLDER_PATTERN.sub(replace, template)

    @staticmethod
    def display_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return "" if value is None else str(value)
