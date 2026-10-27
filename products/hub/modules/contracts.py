from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Protocol


_STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")


def validate_stable_id(value: object, *, label: str) -> str:
    clean = str(value).strip().casefold()
    if not _STABLE_ID.fullmatch(clean):
        raise ValueError(f"{label} must be a stable, namespaced identifier.")
    return clean


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RestartRequired:
    required: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    module_id: str
    display_name: str
    version: str
    category: str
    bundled: bool = True
    enabled_by_default: bool = True
    minimum_core_version: str = "0.3.0-alpha"
    maximum_core_version: str | None = None
    required_core_capabilities: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    optional_capabilities: frozenset[str] = frozenset()
    ui_label: str = ""
    ui_icon: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "module_id",
            validate_stable_id(self.module_id, label="Module ID"),
        )
        for label, value in (
            ("display name", self.display_name),
            ("version", self.version),
            ("category", self.category),
            ("minimum Core version", self.minimum_core_version),
        ):
            if not str(value).strip():
                raise ValueError(f"Module {label} cannot be blank.")
        if self.maximum_core_version is not None and not self.maximum_core_version.strip():
            raise ValueError("Maximum Core version cannot be blank when supplied.")
        if not isinstance(self.bundled, bool) or not isinstance(self.enabled_by_default, bool):
            raise TypeError("Module bundled and enabled defaults must be true or false.")


class ModuleLifecycle(StrEnum):
    DISCOVERED = "discovered"
    DISABLED = "disabled"
    INCOMPATIBLE = "incompatible"
    REGISTERED = "registered"
    CONFIGURED = "configured"
    STARTED = "started"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ModuleStatus:
    module_id: str
    enabled: bool
    desired_enabled: bool
    lifecycle: ModuleLifecycle
    restart_required: bool = False
    detail: str = ""


def _no_op() -> None:
    return None


@dataclass(frozen=True, slots=True)
class ModuleContext:
    """Narrow Core contracts supplied to one module.

    Additional registries are added here only when their Core-owned seams are
    implemented. The callbacks allow a context factory to stage contributions
    and make one module's registration atomic.
    """

    core_version: str
    events: object
    variables: object
    tasks: object
    actions: object
    routine_store: object | None = None
    automation: object | None = None
    trigger_providers: object | None = None
    # Transitional static-catalog seam for providers whose live store remains
    # composition-root owned until a later module-conversion phase.
    trigger_stores: Mapping[str, object] = field(default_factory=dict)
    tabs: object | None = None
    backup: object | None = None
    data_root: object | None = None
    execution_recorder: Callable[[object, str], None] | None = field(
        default=None,
        repr=False,
    )
    trigger_context_enricher: Callable[[], dict[str, object]] | None = field(
        default=None,
        repr=False,
    )
    integration_status_changed: Callable[[str, object], None] | None = field(
        default=None,
        repr=False,
    )
    commit_registration: Callable[[], None] = field(default=_no_op, repr=False)
    rollback_registration: Callable[[], None] = field(default=_no_op, repr=False)


class HubModule(Protocol):
    descriptor: ModuleDescriptor

    def register_catalog(self, context: ModuleContext) -> None: ...

    def register(self, context: ModuleContext) -> None: ...

    def configure(self, context: ModuleContext) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...
