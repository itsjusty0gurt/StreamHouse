from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from products.hub.modules.contracts import (
    HubModule,
    ModuleContext,
    ModuleDescriptor,
    ModuleLifecycle,
    ModuleStatus,
    RestartRequired,
    validate_stable_id,
)
from products.hub.modules.settings import ModuleEnablementStore
from shared.streamhouse_runtime.logger import Logger


_VERSION = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<label>[a-z]+))?$",
    re.IGNORECASE,
)


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        raise ValueError(f'Unsupported Hub version value "{value}".')
    label = (match.group("label") or "").casefold()
    # A plain release sorts after a prerelease with the same numeric version.
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        1 if not label else 0,
        label,
    )


@dataclass(slots=True)
class _ModuleRecord:
    module: HubModule
    enabled: bool
    desired_enabled: bool
    lifecycle: ModuleLifecycle
    detail: str = ""
    context: ModuleContext | None = None


class ModuleRegistry:
    """Core-owned lifecycle and enablement boundary for bundled modules."""

    def __init__(
        self,
        *,
        core_version: str,
        core_capabilities: frozenset[str],
        enablement_store: ModuleEnablementStore,
    ) -> None:
        self.core_version = core_version
        self.core_capabilities = core_capabilities
        self._core_version_key = _version_key(core_version)
        self._enablement_store = enablement_store
        self._records: dict[str, _ModuleRecord] = {}
        self._start_order: list[str] = []

    def register(self, module: HubModule) -> None:
        descriptor = module.descriptor
        module_id = descriptor.module_id
        if module_id in self._records:
            raise ValueError(f'Module "{module_id}" is already registered.')
        if not descriptor.bundled:
            raise ValueError("Hub 0.3 loads only trusted bundled modules.")
        desired = self._enablement_store.desired_enabled(descriptor)
        compatible, detail = self._compatibility(descriptor)
        if not desired:
            lifecycle = ModuleLifecycle.DISABLED
        elif not compatible:
            lifecycle = ModuleLifecycle.INCOMPATIBLE
        else:
            lifecycle = ModuleLifecycle.DISCOVERED
        self._records[module_id] = _ModuleRecord(
            module=module,
            enabled=desired,
            desired_enabled=desired,
            lifecycle=lifecycle,
            detail=detail,
        )

    def _compatibility(self, descriptor: ModuleDescriptor) -> tuple[bool, str]:
        minimum = _version_key(descriptor.minimum_core_version)
        if self._core_version_key < minimum:
            return False, f"Requires Hub Core {descriptor.minimum_core_version} or newer."
        if descriptor.maximum_core_version is not None:
            maximum = _version_key(descriptor.maximum_core_version)
            if self._core_version_key > maximum:
                return False, f"Supports Hub Core through {descriptor.maximum_core_version}."
        missing = sorted(descriptor.required_core_capabilities - self.core_capabilities)
        if missing:
            return False, f"Missing required Core capability: {missing[0]}."
        return True, ""

    def descriptor(self, module_id: str) -> ModuleDescriptor | None:
        record = self._records.get(str(module_id).strip().casefold())
        return record.module.descriptor if record is not None else None

    def descriptors(self) -> tuple[ModuleDescriptor, ...]:
        return tuple(self._records[key].module.descriptor for key in sorted(self._records))

    def status(self, module_id: str) -> ModuleStatus | None:
        record = self._records.get(str(module_id).strip().casefold())
        if record is None:
            return None
        return ModuleStatus(
            module_id=record.module.descriptor.module_id,
            enabled=record.enabled,
            desired_enabled=record.desired_enabled,
            lifecycle=record.lifecycle,
            restart_required=record.enabled != record.desired_enabled,
            detail=record.detail,
        )

    def statuses(self) -> tuple[ModuleStatus, ...]:
        statuses: list[ModuleStatus] = []
        for key in sorted(self._records):
            status = self.status(key)
            if status is not None:
                statuses.append(status)
        return tuple(statuses)

    def is_enabled(self, module_id: str) -> bool:
        status = self.status(module_id)
        return bool(status and status.enabled)

    def set_enabled(self, module_id: str, enabled: bool) -> RestartRequired:
        clean_id = validate_stable_id(module_id, label="Module ID")
        record = self._records.get(clean_id)
        if record is None:
            raise KeyError(f'Module "{clean_id}" is not registered.')
        self._enablement_store.set_enabled(clean_id, enabled)
        record.desired_enabled = enabled
        required = record.enabled != enabled
        return RestartRequired(
            required,
            "Module enablement will change after Hub restarts." if required else "",
        )

    def register_enabled(
        self,
        context_factory: Callable[[ModuleDescriptor], ModuleContext],
    ) -> None:
        for module_id in sorted(self._records):
            record = self._records[module_id]
            if record.lifecycle is not ModuleLifecycle.DISCOVERED:
                continue
            context = context_factory(record.module.descriptor)
            record.context = context
            try:
                record.module.register(context)
                context.commit_registration()
                record.lifecycle = ModuleLifecycle.REGISTERED
            except Exception as error:
                try:
                    context.rollback_registration()
                except Exception as rollback_error:
                    Logger.exception(
                        f'Module "{module_id}" registration rollback failed: {rollback_error}',
                        source="MODULES",
                    )
                self._fail(record, "registration", error)

    def configure_enabled(self) -> None:
        for module_id in sorted(self._records):
            record = self._records[module_id]
            if record.lifecycle is not ModuleLifecycle.REGISTERED or record.context is None:
                continue
            try:
                record.module.configure(record.context)
                record.lifecycle = ModuleLifecycle.CONFIGURED
            except Exception as error:
                try:
                    record.context.rollback_registration()
                except Exception as rollback_error:
                    Logger.exception(
                        f'Module "{module_id}" configuration rollback failed: {rollback_error}',
                        source="MODULES",
                    )
                self._fail(record, "configuration", error)

    def start_enabled(self) -> None:
        for module_id in sorted(self._records):
            record = self._records[module_id]
            if record.lifecycle is not ModuleLifecycle.CONFIGURED:
                continue
            try:
                record.module.start()
                record.lifecycle = ModuleLifecycle.STARTED
                self._start_order.append(module_id)
            except Exception as error:
                try:
                    record.module.stop()
                except Exception as stop_error:
                    Logger.exception(
                        f'Module "{module_id}" partial-start cleanup failed: {stop_error}',
                        source="MODULES",
                    )
                self._fail(record, "startup", error)

    def stop_started(self) -> None:
        while self._start_order:
            module_id = self._start_order.pop()
            record = self._records[module_id]
            try:
                record.module.stop()
                record.lifecycle = ModuleLifecycle.STOPPED
            except Exception as error:
                self._fail(record, "shutdown", error)

    @staticmethod
    def _fail(record: _ModuleRecord, stage: str, error: Exception) -> None:
        record.lifecycle = ModuleLifecycle.FAILED
        record.detail = f"{type(error).__name__}: Module {stage} failed."
        Logger.exception(
            f'Module "{record.module.descriptor.module_id}" {stage} failed.',
            source="MODULES",
        )
