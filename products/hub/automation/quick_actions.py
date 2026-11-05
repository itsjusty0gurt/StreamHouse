from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from shared.streamhouse_runtime.json_store import (
    UnsupportedJsonSchemaError,
    atomic_write_json,
    json_store_exists,
    load_validated_json,
)
from shared.streamhouse_runtime.paths import user_data_root


@dataclass(slots=True)
class QuickAction:
    action_id: str
    label: str
    routine_id: str
    color: str = ""
    icon: str = ""


class QuickActionStore:
    """Core-owned routine shortcuts for the Live workspace."""

    VERSION = 2
    LEGACY_VERSION = 1
    MAX_ACTIONS = 12
    BUTTON_SIZES = ("small", "medium", "large", "extra_large")
    DEFAULT_BUTTON_SIZE = "medium"
    ICONS = ("", "play", "confirm", "stop", "info", "alert")

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "automation" / "quick_actions.json"
        self.actions: list[QuickAction] = []
        self.button_size = self.DEFAULT_BUTTON_SIZE
        self._loaded_legacy = False

    def load(self) -> list[QuickAction]:
        if not json_store_exists(self.path):
            self.actions = []
            self.button_size = self.DEFAULT_BUTTON_SIZE
            return []
        self._loaded_legacy = False
        self.actions = load_validated_json(self.path, self._parse)
        if self._loaded_legacy:
            self.save()
        return deepcopy(self.actions)

    def _parse(self, payload: object) -> list[QuickAction]:
        if not isinstance(payload, dict):
            raise ValueError("Quick Actions must contain a JSON object.")
        version = payload.get("version")
        if type(version) is not int or version not in {
            self.LEGACY_VERSION,
            self.VERSION,
        }:
            raise UnsupportedJsonSchemaError(
                f"Unsupported Quick Actions version {version}; expected "
                f"{self.LEGACY_VERSION} or {self.VERSION}."
            )
        self._loaded_legacy = version == self.LEGACY_VERSION
        self.button_size = self._clean_button_size(
            payload.get("button_size", self.DEFAULT_BUTTON_SIZE)
        )
        values = payload.get("actions", [])
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise ValueError("Quick Actions must contain an action list.")
        actions = [
            QuickAction(
                action_id=str(value.get("action_id", "")).strip(),
                label=str(value.get("label", "")).strip(),
                routine_id=str(value.get("routine_id", "")).strip(),
                color=str(value.get("color", "")).strip(),
                icon=str(value.get("icon", "")).strip(),
            )
            for value in values
        ]
        self._validate(actions)
        return actions

    def save(self) -> None:
        self._validate(self.actions)
        atomic_write_json(
            self.path,
            {
                "version": self.VERSION,
                "button_size": self.button_size,
                "actions": [asdict(item) for item in self.actions],
            },
        )

    def add(
        self,
        label: str,
        routine_id: str,
        color: str = "",
        icon: str = "",
    ) -> QuickAction:
        if len(self.actions) >= self.MAX_ACTIONS:
            raise ValueError(f"Live Quick Actions are limited to {self.MAX_ACTIONS} buttons.")
        action = QuickAction(
            uuid4().hex,
            self._clean_label(label),
            self._clean_routine(routine_id),
            self._clean_color(color),
            self._clean_icon(icon),
        )
        self.actions.append(action)
        self.save()
        return deepcopy(action)

    def update(
        self,
        action_id: str,
        label: str,
        routine_id: str,
        color: str = "",
        icon: str = "",
    ) -> QuickAction:
        action = self._require(action_id)
        action.label = self._clean_label(label)
        action.routine_id = self._clean_routine(routine_id)
        action.color = self._clean_color(color)
        action.icon = self._clean_icon(icon)
        self.save()
        return deepcopy(action)

    def reorder(self, action_id: str, insertion_index: int) -> None:
        action = self._require(action_id)
        old_index = self.actions.index(action)
        target = max(0, min(int(insertion_index), len(self.actions)))
        self.actions.pop(old_index)
        if target > old_index:
            target -= 1
        self.actions.insert(target, action)
        self.save()

    def set_button_size(self, button_size: str) -> None:
        self.button_size = self._clean_button_size(button_size)
        self.save()

    def delete(self, action_id: str) -> bool:
        action = next((item for item in self.actions if item.action_id == action_id), None)
        if action is None:
            return False
        self.actions.remove(action)
        self.save()
        return True

    def _require(self, action_id: str) -> QuickAction:
        action = next((item for item in self.actions if item.action_id == action_id), None)
        if action is None:
            raise ValueError("The selected Quick Action no longer exists.")
        return action

    @classmethod
    def _validate(cls, actions: list[QuickAction]) -> None:
        if len(actions) > cls.MAX_ACTIONS:
            raise ValueError("Quick Actions contains too many buttons.")
        ids: set[str] = set()
        for action in actions:
            if not action.action_id or action.action_id in ids:
                raise ValueError("Quick Actions require unique stable IDs.")
            ids.add(action.action_id)
            action.label = cls._clean_label(action.label)
            action.routine_id = cls._clean_routine(action.routine_id)
            action.color = cls._clean_color(action.color)
            action.icon = cls._clean_icon(action.icon)

    @staticmethod
    def _clean_label(value: str) -> str:
        clean = " ".join(str(value).split())[:60]
        if not clean:
            raise ValueError("A Quick Action label is required.")
        return clean

    @staticmethod
    def _clean_routine(value: str) -> str:
        clean = str(value).strip()
        if not clean:
            raise ValueError("A Quick Action routine is required.")
        return clean

    @staticmethod
    def _clean_color(value: str) -> str:
        clean = str(value).strip().lower()
        if not clean:
            return ""
        if len(clean) != 7 or not clean.startswith("#"):
            raise ValueError("Quick Action colors must use #RRGGBB format.")
        try:
            int(clean[1:], 16)
        except ValueError as error:
            raise ValueError("Quick Action colors must use #RRGGBB format.") from error
        return clean

    @classmethod
    def _clean_icon(cls, value: str) -> str:
        clean = str(value).strip().lower()
        if clean not in cls.ICONS:
            raise ValueError("The selected Quick Action icon is not supported.")
        return clean

    @classmethod
    def _clean_button_size(cls, value: object) -> str:
        clean = str(value).strip().lower()
        if clean not in cls.BUTTON_SIZES:
            raise ValueError("The selected Quick Action button size is not supported.")
        return clean
