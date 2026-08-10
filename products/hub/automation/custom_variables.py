from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Mapping

from products.hub.automation.variables import VARIABLE_INFO
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root


class CustomVariableStore:
    """Global and session automation variables.

    Global values are persisted. Session values intentionally exist only in
    memory and are discarded when Streamhouse Hub closes.
    """

    VERSION = 2
    NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    RESERVED_NAMES = frozenset(VARIABLE_INFO)
    DATA_TYPES = frozenset({"text", "integer", "number", "boolean", "datetime"})

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "automation" / "variables.json"
        self.global_values: dict[str, str] = {}
        self.session_values: dict[str, str] = {}
        self.global_metadata: dict[str, dict[str, str]] = {}
        self.session_metadata: dict[str, dict[str, str]] = {}

    def load(self) -> Mapping[str, str]:
        self.session_values = {}
        self.session_metadata = {}
        if not self.path.exists():
            self.global_values = {}
            self.global_metadata = {}
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
        raw_metadata = payload.get("metadata", {})
        if not isinstance(raw_metadata, dict):
            raise ValueError("Automation variable metadata must be an object.")
        self.global_metadata = {}
        for name, metadata in raw_metadata.items():
            clean_name = self.validate_name(str(name))
            if clean_name not in loaded or not isinstance(metadata, dict):
                continue
            data_type = str(metadata.get("type", "text")).strip().casefold()
            self.global_metadata[clean_name] = {
                "type": data_type if data_type in self.DATA_TYPES else "text",
                "description": str(metadata.get("description", "")).strip()[:500],
            }
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

    def set(
        self,
        scope: str,
        name: str,
        value: object,
        *,
        data_type: str | None = None,
        description: str | None = None,
    ) -> str:
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
        metadata_target = (
            self.global_metadata
            if clean_scope == "global"
            else self.session_metadata
        )
        had_previous = clean_name in target
        previous = target.get(clean_name, "")
        previous_metadata = dict(metadata_target.get(clean_name, {}))
        existing_type = str(metadata_target.get(clean_name, {}).get("type", "text"))
        effective_type = str(data_type or existing_type).strip().casefold()
        target[clean_name] = self.normalize_value(effective_type, value)
        if data_type is not None or description is not None:
            current = metadata_target.setdefault(
                clean_name, {"type": "text", "description": ""}
            )
            if data_type is not None:
                normalized_type = str(data_type).strip().casefold()
                if normalized_type not in self.DATA_TYPES:
                    target[clean_name] = previous if had_previous else ""
                    if not had_previous:
                        target.pop(clean_name, None)
                    raise ValueError(f"Unsupported variable type: {data_type}.")
                current["type"] = normalized_type
            if description is not None:
                current["description"] = str(description).strip()[:500]
        if clean_scope == "global":
            try:
                self.save()
            except OSError:
                if had_previous:
                    target[clean_name] = previous
                else:
                    target.pop(clean_name, None)
                if previous_metadata:
                    metadata_target[clean_name] = previous_metadata
                else:
                    metadata_target.pop(clean_name, None)
                raise
        return clean_name

    def delete(self, name: str) -> bool:
        clean_name = self.validate_name(name)
        if clean_name in self.global_values:
            previous = self.global_values[clean_name]
            previous_metadata = self.global_metadata.pop(clean_name, None)
            del self.global_values[clean_name]
            try:
                self.save()
            except OSError:
                self.global_values[clean_name] = previous
                if previous_metadata is not None:
                    self.global_metadata[clean_name] = previous_metadata
                raise
            return True
        self.session_metadata.pop(clean_name, None)
        return self.session_values.pop(clean_name, None) is not None

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": self.VERSION,
                "global": dict(sorted(self.global_values.items())),
                "metadata": dict(sorted(self.global_metadata.items())),
            },
        )

    @classmethod
    def validate_name(cls, name: str) -> str:
        clean_name = name.strip().casefold()
        if clean_name.startswith("{") and clean_name.endswith("}"):
            clean_name = clean_name[1:-1].strip()
        if clean_name.startswith("custom."):
            clean_name = clean_name.removeprefix("custom.")
        if not cls.NAME_PATTERN.fullmatch(clean_name):
            raise ValueError(
                "Variable names must start with a letter and contain only "
                "lowercase letters, numbers, and underscores."
            )
        if clean_name in cls.RESERVED_NAMES:
            raise ValueError(
                f'Variable name "{clean_name}" is reserved by Streamhouse Hub.'
            )
        return clean_name

    @classmethod
    def validate_custom_name(cls, name: str) -> str:
        clean = name.strip().casefold()
        if clean.startswith("{") and clean.endswith("}"):
            clean = clean[1:-1].strip()
        if "." in clean and not clean.startswith("custom."):
            raise ValueError("Custom variables must use the custom namespace.")
        return cls.validate_name(clean)

    def type_of(self, name: str) -> str:
        clean = self.validate_custom_name(name)
        metadata = self.global_metadata.get(clean) or self.session_metadata.get(clean) or {}
        return str(metadata.get("type", "text"))

    def description_of(self, name: str) -> str:
        clean = self.validate_custom_name(name)
        metadata = self.global_metadata.get(clean) or self.session_metadata.get(clean) or {}
        return str(metadata.get("description", ""))

    @classmethod
    def normalize_value(cls, data_type: str, value: object) -> str:
        clean_type = str(data_type).strip().casefold()
        if clean_type not in cls.DATA_TYPES:
            raise ValueError(f"Unsupported variable type: {data_type}.")
        if clean_type == "text":
            return str(value)
        if clean_type == "integer":
            if isinstance(value, bool):
                raise ValueError("Boolean values are not integers.")
            numeric = float(str(value).strip())
            if not numeric.is_integer():
                raise ValueError("Integer variables require a whole number.")
            return str(int(numeric))
        if clean_type == "number":
            if isinstance(value, bool):
                raise ValueError("Boolean values are not numbers.")
            return f"{float(str(value).strip()):g}"
        if clean_type == "boolean":
            clean = str(value).strip().casefold()
            if clean in {"1", "true", "yes", "on"}:
                return "true"
            if clean in {"0", "false", "no", "off"}:
                return "false"
            raise ValueError("Boolean values must be true/false, yes/no, on/off, or 1/0.")
        clean = str(value).strip()
        try:
            datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Datetime values must use ISO 8601 format.") from error
        return clean

    @classmethod
    def validate_generated_name(cls, name: str) -> str:
        """Validate a routine-scoped generated output, including reserved names."""
        clean_name = name.strip().casefold()
        if clean_name.startswith("{") and clean_name.endswith("}"):
            clean_name = clean_name[1:-1].strip()
        if not cls.NAME_PATTERN.fullmatch(clean_name):
            raise ValueError(
                "Generated variable names must start with a letter and contain only "
                "lowercase letters, numbers, and underscores."
            )
        return clean_name

    @classmethod
    def generated_names(
        cls,
        task_type: str,
        config: Mapping[str, object],
    ) -> tuple[str, ...]:
        """Return the template names created by an automation task."""
        normalized_type = task_type.strip().casefold()
        if normalized_type.startswith("counter."):
            from products.hub.counters.tasks import generated_names

            return generated_names(dict(config))
        if normalized_type == "twitch.get_channel_information_field":
            field_id = str(config.get("field", "")).strip().casefold()
            requested = str(config.get("output_variable", "")).strip()
            try:
                name = cls.validate_generated_name(requested or field_id)
            except ValueError:
                return ()
            return (
                name,
                f"{name}_status",
                "channel_information_available",
                "channel_information_status",
            )
        if normalized_type == "twitch.build_social_links_message":
            requested = str(config.get("output_variable", "")).strip()
            try:
                name = cls.validate_generated_name(
                    requested or "social_links_message"
                )
            except ValueError:
                return ()
            return (
                name,
                f"{name}_status",
                "channel_information_available",
                "channel_information_status",
            )
        key = {
            "core.create_global_variable": "name",
            "core.create_session_variable": "name",
            "core.create_routine_variable": "name",
            "core.logic_get_input": "name",
            "core.logic_random_number": "name",
            "core.file_read": "variable",
            "core.file_random_line": "variable",
            "core.file_specific_line": "variable",
            "core.path_exists": "variable",
            "core.file_count_lines": "variable",
            "core.format_duration": "output_variable",
            "core.select_text": "output_variable",
        }.get(normalized_type)
        if key is None:
            return ()
        try:
            validator = (
                cls.validate_generated_name
                if normalized_type in {"core.format_duration", "core.select_text"}
                else cls.validate_name
            )
            name = validator(str(config.get(key, "")))
        except ValueError:
            return ()
        if normalized_type == "core.logic_get_input":
            return name, f"{name}_accepted"
        if normalized_type == "core.format_duration":
            return name, f"{name}_status"
        return (name,)
