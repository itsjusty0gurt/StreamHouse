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
LEGACY_VARIABLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
PLACEHOLDER_PATTERN = re.compile(
    r"\{([a-z][a-z0-9_]{0,127}|[a-z][a-z0-9_]{0,63}(?:\.[a-z][a-z0-9_]{0,63})+)\}"
)
RESERVED_NAMESPACES = frozenset(
    {
        "stream",
        "user",
        "chat",
        "counter",
        "obs",
        "hub",
        "custom",
        "automation",
        "ads",
        "soundboard",
    }
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
    TEMPORARY = "temporary"


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
    required_context: tuple[str, ...] = ()
    preview_value: Any | None = None
    alias_of: str = ""
    legacy: bool = False

    def __post_init__(self) -> None:
        name = self.name.strip().casefold()
        if not VARIABLE_NAME_PATTERN.fullmatch(name) and not (
            self.legacy and LEGACY_VARIABLE_NAME_PATTERN.fullmatch(name)
        ):
            raise ValueError(f'Invalid canonical variable name: "{self.name}".')
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "data_type", VariableDataType(self.data_type))
        object.__setattr__(
            self, "availability", VariableAvailability(self.availability)
        )
        alias_of = self.alias_of.strip().casefold()
        if alias_of:
            if alias_of == name or not VARIABLE_NAME_PATTERN.fullmatch(alias_of):
                raise ValueError(f'Invalid variable alias target: "{self.alias_of}".')
            object.__setattr__(self, "alias_of", alias_of)
        object.__setattr__(
            self,
            "required_context",
            tuple(
                dict.fromkeys(
                    str(item).strip().casefold()
                    for item in self.required_context
                    if str(item).strip()
                )
            ),
        )

    @property
    def placeholder(self) -> str:
        return f"{{{self.name}}}"

    @property
    def is_alias(self) -> bool:
        return bool(self.alias_of)


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
        self._aliases: dict[str, VariableDefinition] = {}

    def register(self, provider: VariableProvider) -> None:
        existing = {item.name for item in self.all_definitions()}
        offered = provider.definitions()
        names = [item.name for item in offered]
        duplicate = next(
            (name for name in names if name in existing or names.count(name) > 1),
            "",
        )
        if duplicate:
            raise ValueError(f'Variable "{duplicate}" is already registered.')
        self._providers.append(provider)
        try:
            self._catalog(include_aliases=True)
        except (TypeError, ValueError):
            self._providers.pop()
            raise

    def register_alias(
        self,
        name: str,
        target: str,
        *,
        display_name: str = "",
        description: str = "",
        legacy: bool = False,
    ) -> VariableDefinition:
        clean_name = validate_variable_name(name, allow_legacy=legacy)
        clean_target = validate_variable_name(target)
        catalog = {item.name: item for item in self.all_definitions()}
        if clean_name in catalog:
            raise ValueError(f'Variable "{clean_name}" is already registered.')
        if clean_target not in catalog:
            raise ValueError(f'Alias target "{clean_target}" is not registered.')
        canonical = self._canonical_definition(clean_target, catalog)
        alias = VariableDefinition(
            name=clean_name,
            display_name=display_name or canonical.display_name,
            description=description or f"Compatibility alias for {canonical.name}.",
            data_type=canonical.data_type,
            source=canonical.source,
            category=canonical.category,
            availability=canonical.availability,
            writable=canonical.writable,
            default=canonical.default,
            required_context=canonical.required_context,
            preview_value=canonical.preview_value,
            alias_of=canonical.name,
            legacy=legacy,
        )
        self._aliases[clean_name] = alias
        # Rebuild now so collisions and accidental alias chains fail immediately.
        self._catalog(include_aliases=True)
        return alias

    def definitions(
        self, *, include_aliases: bool = False
    ) -> tuple[VariableDefinition, ...]:
        catalog = self._catalog(include_aliases=True)
        return tuple(
            definition for definition in catalog.values()
            if include_aliases or not definition.is_alias
        )

    def all_definitions(self) -> tuple[VariableDefinition, ...]:
        return self.definitions(include_aliases=True)

    def aliases(self) -> tuple[VariableDefinition, ...]:
        return tuple(item for item in self.all_definitions() if item.is_alias)

    def definition(self, name: str) -> VariableDefinition | None:
        clean = str(name).strip().casefold().removeprefix("{").removesuffix("}")
        return self._catalog(include_aliases=True).get(clean)

    def _catalog(self, *, include_aliases: bool) -> dict[str, VariableDefinition]:
        catalog: dict[str, VariableDefinition] = {}
        for provider in self._providers:
            for definition in provider.definitions():
                if definition.name in catalog:
                    raise ValueError(
                        f'Variable "{definition.name}" is already registered.'
                    )
                catalog[definition.name] = definition
        for name, definition in self._aliases.items():
            if name in catalog:
                raise ValueError(f'Variable "{name}" is already registered.')
            catalog[name] = definition
        for definition in tuple(catalog.values()):
            if definition.alias_of:
                self._canonical_definition(definition.name, catalog)
        if include_aliases:
            return catalog
        return {name: item for name, item in catalog.items() if not item.is_alias}

    @staticmethod
    def _canonical_definition(
        name: str, catalog: Mapping[str, VariableDefinition]
    ) -> VariableDefinition:
        current = name
        seen: set[str] = set()
        while True:
            if current in seen:
                raise ValueError(f'Variable alias loop detected at "{current}".')
            seen.add(current)
            definition = catalog.get(current)
            if definition is None:
                raise ValueError(f'Alias target "{current}" is not registered.')
            if not definition.alias_of:
                return definition
            current = definition.alias_of

    def resolve(
        self, name: str, context: Mapping[str, object] | None = None
    ) -> VariableSnapshot | None:
        clean = name.strip().casefold().removeprefix("{").removesuffix("}")
        catalog = self._catalog(include_aliases=True)
        requested = catalog.get(clean)
        if requested is None:
            return None
        canonical = self._canonical_definition(clean, catalog)
        for provider in self._providers:
            if canonical.name in {item.name for item in provider.definitions()}:
                snapshot = provider.resolve(canonical.name, context or {})
                if requested.is_alias:
                    return VariableSnapshot(requested, snapshot.value, snapshot.available, snapshot.detail)
                return snapshot
        return None

    def snapshots(
        self, context: Mapping[str, object] | None = None
    ) -> tuple[VariableSnapshot, ...]:
        resolved: list[VariableSnapshot] = []
        for definition in self.definitions():
            snapshot = self.resolve(definition.name, context)
            if snapshot is not None:
                resolved.append(snapshot)
        return tuple(resolved)

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        snapshot = self.resolve(name)
        if snapshot is None:
            raise KeyError(f'Variable "{name}" is not registered.')
        if not snapshot.definition.writable:
            raise PermissionError(f'Variable "{snapshot.definition.name}" is read-only.')
        catalog = self._catalog(include_aliases=True)
        canonical = self._canonical_definition(snapshot.definition.name, catalog)
        for provider in self._providers:
            if canonical.name in {item.name for item in provider.definitions()}:
                result = provider.set_value(canonical.name, value)
                return self.resolve(snapshot.definition.name) or result
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


def validate_variable_name(name: str, *, allow_legacy: bool = False) -> str:
    clean = str(name).strip().casefold().removeprefix("{").removesuffix("}")
    if not VARIABLE_NAME_PATTERN.fullmatch(clean) and not (
        allow_legacy and LEGACY_VARIABLE_NAME_PATTERN.fullmatch(clean)
    ):
        kind = "legacy variable" if allow_legacy else "canonical variable"
        raise ValueError(f'Invalid {kind} name: "{name}".')
    return clean


def placeholder_names(template: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in PLACEHOLDER_PATTERN.finditer(str(template)))


def render_placeholders(
    template: str,
    values: Mapping[str, object],
    *,
    fallback: str | None = None,
    strip_values: bool = False,
) -> str:
    def replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            return match.group(0) if fallback is None else fallback
        rendered = VariableRegistry.display_value(values[name])
        return rendered.strip() if strip_values else rendered

    return PLACEHOLDER_PATTERN.sub(
        replacement,
        str(template),
    )
