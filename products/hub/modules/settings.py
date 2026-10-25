from __future__ import annotations

from pathlib import Path
from typing import Any

from products.hub.modules.contracts import ModuleDescriptor, validate_stable_id
from shared.streamhouse_runtime.json_store import (
    atomic_write_json,
    json_store_exists,
    load_validated_json,
)
from shared.streamhouse_runtime.paths import user_data_root


class ModuleEnablementStore:
    """Core-owned next-start enablement preferences for bundled modules."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            Path(path)
            if path is not None
            else user_data_root() / "config" / "modules.json"
        )
        self._enabled = self._load() if json_store_exists(self.path) else {}

    @classmethod
    def _parse(cls, payload: Any) -> dict[str, bool]:
        if not isinstance(payload, dict) or payload.get("version") != cls.SCHEMA_VERSION:
            raise ValueError("Unsupported module enablement settings version.")
        enabled = payload.get("enabled")
        if not isinstance(enabled, dict):
            raise ValueError("Module enablement settings must contain an enabled map.")
        result: dict[str, bool] = {}
        for module_id, value in enabled.items():
            clean_id = validate_stable_id(module_id, label="Module ID")
            if not isinstance(value, bool):
                raise ValueError(f'Enabled state for "{clean_id}" must be true or false.')
            result[clean_id] = value
        return result

    def _load(self) -> dict[str, bool]:
        return load_validated_json(self.path, self._parse)

    def desired_enabled(self, descriptor: ModuleDescriptor) -> bool:
        return self._enabled.get(descriptor.module_id, descriptor.enabled_by_default)

    def set_enabled(self, module_id: str, enabled: bool) -> None:
        clean_id = validate_stable_id(module_id, label="Module ID")
        if not isinstance(enabled, bool):
            raise TypeError("Module enabled state must be true or false.")
        self._enabled[clean_id] = enabled
        atomic_write_json(
            self.path,
            {
                "version": self.SCHEMA_VERSION,
                "enabled": dict(sorted(self._enabled.items())),
            },
        )

    def overrides(self) -> dict[str, bool]:
        return dict(self._enabled)
