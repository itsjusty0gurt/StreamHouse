from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from products.hub.modules.contracts import Availability, validate_stable_id


@dataclass(frozen=True, slots=True)
class ModuleTabContribution:
    tab_id: str
    owner_module_id: str
    label: str
    factory: Callable[[object], object] = field(repr=False, compare=False)
    order: int = 500

    def __post_init__(self) -> None:
        object.__setattr__(self, "tab_id", validate_stable_id(self.tab_id, label="Tab ID"))
        object.__setattr__(
            self,
            "owner_module_id",
            validate_stable_id(self.owner_module_id, label="Owner module ID"),
        )
        if not self.label.strip():
            raise ValueError("Module tab label cannot be blank.")


class ModuleTabRegistry:
    def __init__(self) -> None:
        self._tabs: dict[str, ModuleTabContribution] = {}

    def register(self, contribution: ModuleTabContribution) -> None:
        if contribution.tab_id in self._tabs:
            raise ValueError(f'Tab "{contribution.tab_id}" is already registered.')
        self._tabs[contribution.tab_id] = contribution

    def contributions(self) -> tuple[ModuleTabContribution, ...]:
        return tuple(
            sorted(self._tabs.values(), key=lambda item: (item.order, item.tab_id))
        )


@dataclass(frozen=True, slots=True)
class TriggerProviderContribution:
    provider_id: str
    owner_module_id: str
    display_name: str
    store: object = field(repr=False, compare=False)
    event_types: Mapping[str, str] = field(default_factory=dict)
    editor_service: object | None = field(default=None, repr=False, compare=False)
    availability: Callable[[], Availability] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            validate_stable_id(self.provider_id, label="Trigger provider ID"),
        )
        object.__setattr__(
            self,
            "owner_module_id",
            validate_stable_id(self.owner_module_id, label="Owner module ID"),
        )
        if not self.display_name.strip():
            raise ValueError("Trigger provider display name cannot be blank.")

    def current_availability(self) -> Availability:
        return self.availability() if self.availability is not None else Availability(True)


class TriggerProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, TriggerProviderContribution] = {}

    def register(self, contribution: TriggerProviderContribution) -> None:
        if contribution.provider_id in self._providers:
            raise ValueError(
                f'Trigger provider "{contribution.provider_id}" is already registered.'
            )
        self._providers[contribution.provider_id] = contribution

    def get(self, provider_id: str) -> TriggerProviderContribution | None:
        return self._providers.get(str(provider_id).strip().casefold())

    def contributions(self) -> tuple[TriggerProviderContribution, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))


@dataclass(frozen=True, slots=True)
class BackupContributor:
    contributor_id: str
    owner_module_id: str
    display_name: str
    schema_version: int
    export: Callable[[object], Mapping[str, Any]] = field(repr=False, compare=False)
    validate: Callable[[object], None] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contributor_id",
            validate_stable_id(self.contributor_id, label="Backup contributor ID"),
        )
        object.__setattr__(
            self,
            "owner_module_id",
            validate_stable_id(self.owner_module_id, label="Owner module ID"),
        )
        if not self.display_name.strip() or self.schema_version < 1:
            raise ValueError("Backup contributor metadata is invalid.")


class BackupContributorRegistry:
    def __init__(self) -> None:
        self._contributors: dict[str, BackupContributor] = {}

    def register(self, contribution: BackupContributor) -> None:
        if contribution.contributor_id in self._contributors:
            raise ValueError(
                f'Backup contributor "{contribution.contributor_id}" is already registered.'
            )
        self._contributors[contribution.contributor_id] = contribution

    def get(self, contributor_id: str) -> BackupContributor | None:
        return self._contributors.get(str(contributor_id).strip().casefold())

    def contributions(self) -> tuple[BackupContributor, ...]:
        return tuple(self._contributors[key] for key in sorted(self._contributors))
