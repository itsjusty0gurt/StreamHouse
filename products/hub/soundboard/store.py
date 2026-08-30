from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import RLock

from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root
from products.hub.soundboard.models import SoundboardButton, SoundboardPage


class SoundboardStore:
    """Persistent pages and routine-backed viewer buttons."""

    VERSION = 1
    MAX_BUTTONS_PER_PAGE = 9
    MAX_PAGES = 20

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "twitch" / "soundboard.json"
        self.pages: list[SoundboardPage] = []
        self._lock = RLock()

    def load(self) -> list[SoundboardPage]:
        with self._lock:
            if not self.path.exists():
                self.pages = [SoundboardPage.create("Sounds")]
                return deepcopy(self.pages)
            payload = load_json_with_backup(self.path)
            if not isinstance(payload, dict):
                raise ValueError("Soundboard data must contain a JSON object.")
            version = payload.get("version")
            if type(version) is not int or version != self.VERSION:
                raise ValueError(
                    f"Unsupported soundboard version {version}; expected {self.VERSION}."
                )
            values = payload.get("pages", [])
            if not isinstance(values, list):
                raise ValueError("Soundboard data must contain a page list.")
            pages = [
                SoundboardPage.from_dict(value)
                for value in values
                if isinstance(value, dict)
            ]
            self._validate(pages)
            self.pages = pages or [SoundboardPage.create("Sounds")]
            return deepcopy(self.pages)

    def save(self) -> None:
        with self._lock:
            self._validate(self.pages)
            atomic_write_json(
                self.path,
                {
                    "version": self.VERSION,
                    "pages": [asdict(page) for page in self.pages],
                },
            )

    def snapshot(self) -> list[SoundboardPage]:
        with self._lock:
            return deepcopy(self.pages)

    def get_page(self, page_id: str) -> SoundboardPage | None:
        with self._lock:
            page = self._find_page(self.pages, page_id)
            return deepcopy(page) if page else None

    def get_button(
        self, button_id: str
    ) -> tuple[SoundboardPage, SoundboardButton] | None:
        with self._lock:
            for page in self.pages:
                for button in page.buttons:
                    if button.button_id == button_id:
                        return deepcopy(page), deepcopy(button)
        return None

    def add_page(self, name: str) -> SoundboardPage:
        with self._lock:
            if len(self.pages) >= self.MAX_PAGES:
                raise ValueError(f"Soundboards are limited to {self.MAX_PAGES} pages.")
            page = SoundboardPage.create(self._clean_name(name, "Page name"))
            self.pages.append(page)
            self.save()
            return deepcopy(page)

    def rename_page(self, page_id: str, name: str) -> SoundboardPage:
        with self._lock:
            page = self._require_page(page_id)
            page.name = self._clean_name(name, "Page name")
            self.save()
            return deepcopy(page)

    def delete_page(self, page_id: str) -> bool:
        with self._lock:
            page = self._find_page(self.pages, page_id)
            if page is None:
                return False
            self.pages.remove(page)
            if not self.pages:
                self.pages.append(SoundboardPage.create("Sounds"))
            self.save()
            return True

    def move_page(self, page_id: str, offset: int) -> SoundboardPage:
        with self._lock:
            page = self._require_page(page_id)
            current = self.pages.index(page)
            target = max(0, min(current + int(offset), len(self.pages) - 1))
            self.pages.pop(current)
            self.pages.insert(target, page)
            self.save()
            return deepcopy(page)

    def add_button(
        self, page_id: str, label: str, routine_id: str = ""
    ) -> SoundboardButton:
        with self._lock:
            page = self._require_page(page_id)
            if len(page.buttons) >= self.MAX_BUTTONS_PER_PAGE:
                raise ValueError("Each soundboard page can contain up to 9 sounds.")
            button = SoundboardButton.create(
                self._clean_name(label, "Button label"), routine_id
            )
            page.buttons.append(button)
            self.save()
            return deepcopy(button)

    def update_button(
        self,
        button_id: str,
        *,
        label: str | None = None,
        routine_id: str | None = None,
        enabled: bool | None = None,
    ) -> SoundboardButton:
        with self._lock:
            button = self._require_button(button_id)
            if label is not None:
                button.label = self._clean_name(label, "Button label")
            if routine_id is not None:
                button.routine_id = routine_id.strip()
            if enabled is not None:
                button.enabled = bool(enabled)
            self.save()
            return deepcopy(button)

    def delete_button(self, button_id: str) -> bool:
        with self._lock:
            for page in self.pages:
                for button in page.buttons:
                    if button.button_id == button_id:
                        page.buttons.remove(button)
                        self.save()
                        return True
        return False

    def move_button(self, button_id: str, offset: int) -> SoundboardButton:
        with self._lock:
            for page in self.pages:
                for button in page.buttons:
                    if button.button_id != button_id:
                        continue
                    current = page.buttons.index(button)
                    target = max(0, min(current + int(offset), len(page.buttons) - 1))
                    page.buttons.pop(current)
                    page.buttons.insert(target, button)
                    self.save()
                    return deepcopy(button)
        raise ValueError("The selected soundboard button no longer exists.")

    def public_config(self) -> dict[str, object]:
        """Return viewer-safe data without routine IDs or local file paths."""
        with self._lock:
            return {
                "version": self.VERSION,
                "pages": [
                    {
                        "id": page.page_id,
                        "name": page.name,
                        "buttons": [
                            {"id": button.button_id, "label": button.label}
                            for button in page.buttons
                            if button.enabled and button.routine_id
                        ],
                    }
                    for page in self.pages
                ],
            }

    @classmethod
    def _validate(cls, pages: list[SoundboardPage]) -> None:
        if len(pages) > cls.MAX_PAGES:
            raise ValueError("Soundboard contains too many pages.")
        page_ids: set[str] = set()
        button_ids: set[str] = set()
        for page in pages:
            if not page.page_id:
                raise ValueError("Soundboard pages require stable IDs.")
            page.name = cls._clean_name(page.name, "Page name")
            if page.page_id in page_ids:
                raise ValueError("Soundboard page IDs must be unique.")
            page_ids.add(page.page_id)
            if len(page.buttons) > cls.MAX_BUTTONS_PER_PAGE:
                raise ValueError("A soundboard page contains more than 9 sounds.")
            for button in page.buttons:
                if not button.button_id:
                    raise ValueError("Soundboard buttons require stable IDs.")
                button.label = cls._clean_name(button.label, "Button label")
                if button.button_id in button_ids:
                    raise ValueError("Soundboard button IDs must be unique.")
                button_ids.add(button.button_id)

    def _require_page(self, page_id: str) -> SoundboardPage:
        page = self._find_page(self.pages, page_id)
        if page is None:
            raise ValueError("The selected soundboard page no longer exists.")
        return page

    def _require_button(self, button_id: str) -> SoundboardButton:
        for page in self.pages:
            for button in page.buttons:
                if button.button_id == button_id:
                    return button
        raise ValueError("The selected soundboard button no longer exists.")

    @staticmethod
    def _find_page(
        pages: list[SoundboardPage], page_id: str
    ) -> SoundboardPage | None:
        return next((page for page in pages if page.page_id == page_id), None)

    @staticmethod
    def _clean_name(value: str, field: str) -> str:
        clean = " ".join(str(value).split())[:60]
        if not clean:
            raise ValueError(f"{field} is required.")
        return clean
