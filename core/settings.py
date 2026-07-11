from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar


@dataclass(slots=True)
class AppSettings:
    """User-configurable Sally application preferences."""

    STARTUP_PAGES: ClassVar[tuple[str, ...]] = (
        "Dashboard",
        "Twitch",
        "Logs",
        "Settings",
    )
    LOG_LEVELS: ClassVar[tuple[str, ...]] = (
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    )

    startup_page: str = "Dashboard"
    log_level: str = "DEBUG"
    ui_log_limit: int = 2000
    show_developer_tools: bool = True

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> AppSettings:
        """Create validated settings from serialized values."""

        defaults = cls()

        startup_page = values.get("startup_page", defaults.startup_page)
        if startup_page not in cls.STARTUP_PAGES:
            startup_page = defaults.startup_page

        log_level = values.get("log_level", defaults.log_level)
        if log_level not in cls.LOG_LEVELS:
            log_level = defaults.log_level

        ui_log_limit = values.get("ui_log_limit", defaults.ui_log_limit)
        if not isinstance(ui_log_limit, int) or isinstance(ui_log_limit, bool):
            ui_log_limit = defaults.ui_log_limit
        ui_log_limit = min(max(ui_log_limit, 100), 10_000)

        show_developer_tools = values.get(
            "show_developer_tools",
            defaults.show_developer_tools,
        )
        if not isinstance(show_developer_tools, bool):
            show_developer_tools = defaults.show_developer_tools

        return cls(
            startup_page=startup_page,
            log_level=log_level,
            ui_log_limit=ui_log_limit,
            show_developer_tools=show_developer_tools,
        )


class SettingsStore:
    """Load and save Sally preferences as a small JSON document."""

    def __init__(self, path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parent.parent
        self.path = path or project_root / "config" / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()

        with self.path.open(encoding="utf-8") as settings_file:
            values = json.load(settings_file)

        if not isinstance(values, dict):
            raise ValueError("Settings file must contain a JSON object.")

        return AppSettings.from_dict(values)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")

        with temporary_path.open("w", encoding="utf-8") as settings_file:
            json.dump(asdict(settings), settings_file, indent=2)
            settings_file.write("\n")

        temporary_path.replace(self.path)
