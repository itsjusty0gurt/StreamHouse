from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4


@dataclass(slots=True)
class SoundboardButton:
    button_id: str
    label: str
    routine_id: str
    enabled: bool = True

    @classmethod
    def create(cls, label: str, routine_id: str = "") -> SoundboardButton:
        return cls(uuid4().hex, label.strip(), routine_id.strip())

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> SoundboardButton:
        return cls(
            button_id=str(values.get("button_id", "")) or uuid4().hex,
            label=str(values.get("label", "")).strip(),
            routine_id=str(values.get("routine_id", "")).strip(),
            enabled=bool(values.get("enabled", True)),
        )


@dataclass(slots=True)
class SoundboardPage:
    page_id: str
    name: str
    buttons: list[SoundboardButton] = field(default_factory=list)

    @classmethod
    def create(cls, name: str) -> SoundboardPage:
        return cls(uuid4().hex, name.strip())

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> SoundboardPage:
        raw_buttons = values.get("buttons", [])
        return cls(
            page_id=str(values.get("page_id", "")) or uuid4().hex,
            name=str(values.get("name", "")).strip(),
            buttons=[
                SoundboardButton.from_dict(button)
                for button in raw_buttons
                if isinstance(button, dict)
            ]
            if isinstance(raw_buttons, list)
            else [],
        )
