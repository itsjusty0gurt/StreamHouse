from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from automation.variables import VARIABLE_INFO
from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root


class CustomVariableStore:
    """Global and session automation variables.

    Global values are persisted. Session values intentionally exist only in
    memory and are discarded when Sally closes.
    """

    VERSION = 1
    NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    RESERVED_NAMES = frozenset(VARIABLE_INFO)

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "automation" / "variables.json"
        self.global_values: dict[str, str] = {}
        self.session_values: dict[str, str] = {}

    def load(self) -> Mapping[str, str]:
        self.session_values = {}
        if not self.path.exists():
            self.global_values = {}
            return self.values()
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Automation variables must contain a JSON object.")
        version = int(payload.get("version", 1))
        if version > self.VERSION:
            raise ValueError("Automation variable data is newer than this app.")
        raw_values = payload.get("global", {})
        if not isinstance(raw_values, dict):
            raise ValueError("Global automation variables must be an object.")
        loaded: dict[str, str] = {}
        for name, value in raw_values.items():
            clean_name = self.validate_name(str(name))
            loaded[clean_name] = str(value)
        self.global_values = loaded
        return self.values()

    def values(self) -> dict[str, str]:
        return {**self.global_values, **self.session_values}

    def scope_of(self, name: str) -> str:
        clean_name = name.strip().casefold()
        if clean_name in self.global_values:
            return "global"
        if clean_name in self.session_values:
            return "session"
        return ""

    def set(self, scope: str, name: str, value: object) -> str:
        clean_scope = scope.strip().casefold()
        if clean_scope not in {"global", "session"}:
            raise ValueError("Stored variables must be global or session variables.")
        clean_name = self.validate_name(name)
        existing_scope = self.scope_of(clean_name)
        if existing_scope and existing_scope != clean_scope:
            raise ValueError(
                f'Variable "{clean_name}" already exists as a {existing_scope} variable.'
            )
        target = (
            self.global_values if clean_scope == "global" else self.session_values
        )
        had_previous = clean_name in target
        previous = target.get(clean_name, "")
        target[clean_name] = str(value)
        if clean_scope == "global":
            try:
                self.save()
            except OSError:
                if had_previous:
                    target[clean_name] = previous
                else:
                    target.pop(clean_name, None)
                raise
        return clean_name

    def delete(self, name: str) -> bool:
        clean_name = self.validate_name(name)
        if clean_name in self.global_values:
            previous = self.global_values[clean_name]
            del self.global_values[clean_name]
            try:
                self.save()
            except OSError:
                self.global_values[clean_name] = previous
                raise
            return True
        return self.session_values.pop(clean_name, None) is not None

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": self.VERSION,
                "global": dict(sorted(self.global_values.items())),
            },
        )

    @classmethod
    def validate_name(cls, name: str) -> str:
        clean_name = name.strip().casefold()
        if not cls.NAME_PATTERN.fullmatch(clean_name):
            raise ValueError(
                "Variable names must start with a letter and contain only "
                "lowercase letters, numbers, and underscores."
            )
        if clean_name in cls.RESERVED_NAMES:
            raise ValueError(f'Variable name "{clean_name}" is reserved by Sally.')
        return clean_name
