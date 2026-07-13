from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar

from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root


@dataclass(slots=True)
class AppSettings:
    """User-configurable Sally application preferences."""

    STARTUP_PAGES: ClassVar[tuple[str, ...]] = (
        "Dashboard",
        "Twitch",
        "AI",
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
    twitch_chat_show_timestamps: bool = True
    twitch_chat_font_family: str = "Segoe UI"
    twitch_chat_font_size: int = 10

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> AppSettings:
        """Create validated settings from serialized values."""

        defaults = cls()

        startup_page = values.get("startup_page", defaults.startup_page)
        if startup_page == "Memories":
            startup_page = "AI"
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

        show_timestamps = values.get(
            "twitch_chat_show_timestamps",
            defaults.twitch_chat_show_timestamps,
        )
        if not isinstance(show_timestamps, bool):
            show_timestamps = defaults.twitch_chat_show_timestamps

        font_family = values.get(
            "twitch_chat_font_family",
            defaults.twitch_chat_font_family,
        )
        if not isinstance(font_family, str) or not font_family.strip():
            font_family = defaults.twitch_chat_font_family
        font_family = font_family.strip()[:100]

        font_size = values.get(
            "twitch_chat_font_size",
            defaults.twitch_chat_font_size,
        )
        if not isinstance(font_size, int) or isinstance(font_size, bool):
            font_size = defaults.twitch_chat_font_size
        font_size = min(max(font_size, 8), 24)

        return cls(
            startup_page=startup_page,
            log_level=log_level,
            ui_log_limit=ui_log_limit,
            show_developer_tools=show_developer_tools,
            twitch_chat_show_timestamps=show_timestamps,
            twitch_chat_font_family=font_family,
            twitch_chat_font_size=font_size,
        )


class SettingsStore:
    """Load and save Sally preferences as a small JSON document."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "config" / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()

        values = load_json_with_backup(self.path)

        if not isinstance(values, dict):
            raise ValueError("Settings file must contain a JSON object.")

        return AppSettings.from_dict(values)

    def save(self, settings: AppSettings) -> None:
        payload = asdict(settings)
        payload["_version"] = 1
        atomic_write_json(self.path, payload)
