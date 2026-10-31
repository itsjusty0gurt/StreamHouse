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


class QuickActionStore:
    """Core-owned routine shortcuts for the Live workspace."""

    VERSION = 1
    MAX_ACTIONS = 12

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "automation" / "quick_actions.json"
        self.actions: list[QuickAction] = []

    def load(self) -> list[QuickAction]:
        if not json_store_exists(self.path):
            self.actions = []
            return []
        self.actions = load_validated_json(self.path, self._parse)
        return deepcopy(self.actions)

    def _parse(self, payload: object) -> list[QuickAction]:
        if not isinstance(payload, dict):
            raise ValueError("Quick Actions must contain a JSON object.")
        version = payload.get("version")
        if type(version) is not int or version != self.VERSION:
            raise UnsupportedJsonSchemaError(
                f"Unsupported Quick Actions version {version}; expected {self.VERSION}."
            )
        values = payload.get("actions", [])
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise ValueError("Quick Actions must contain an action list.")
        actions = [
            QuickAction(
                action_id=str(value.get("action_id", "")).strip(),
                label=str(value.get("label", "")).strip(),
                routine_id=str(value.get("routine_id", "")).strip(),
            )
            for value in values
        ]
        self._validate(actions)
        return actions

    def save(self) -> None:
        self._validate(self.actions)
        atomic_write_json(
            self.path,
            {"version": self.VERSION, "actions": [asdict(item) for item in self.actions]},
        )

    def add(self, label: str, routine_id: str) -> QuickAction:
        if len(self.actions) >= self.MAX_ACTIONS:
            raise ValueError(f"Live Quick Actions are limited to {self.MAX_ACTIONS} buttons.")
        action = QuickAction(uuid4().hex, self._clean_label(label), self._clean_routine(routine_id))
        self.actions.append(action)
        self.save()
        return deepcopy(action)

    def update(self, action_id: str, label: str, routine_id: str) -> QuickAction:
        action = self._require(action_id)
        action.label = self._clean_label(label)
        action.routine_id = self._clean_routine(routine_id)
        self.save()
        return deepcopy(action)

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
