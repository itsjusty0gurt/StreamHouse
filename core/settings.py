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
    local_ai_enabled: bool = True
    local_ai_endpoint: str = "http://127.0.0.1:11434"
    local_ai_model: str = "qwen3:14b"
    ai_viewer_memory_enabled: bool = False
    ai_memory_reasoning_enabled: bool = True
    ai_memory_message_threshold: int = 10
    ai_memory_reset_hour: int = 4
    ai_memory_reset_minute: int = 0
    ai_memory_promo_enabled: bool = True
    ai_memory_promo_interval_messages: int = 150
    ai_response_decisions_enabled: bool = True
    ai_auto_send_replies: bool = False
    ai_response_max_age_seconds: int = 15
    ai_response_min_interval_seconds: int = 8
    ai_conversation_followup_seconds: int = 180
    ai_interjections_enabled: bool = True
    ai_interjection_min_interval_seconds: int = 180
    ai_personality: str = (
        "Warm, quick-witted, curious, and conversational. Match the streamer's "
        "energy, keep replies concise, and sound like a genuine cohost rather "
        "than a customer-service assistant."
    )
    ai_allow_mild_profanity: bool = False
    ai_allow_strong_profanity: bool = False

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

        local_ai_enabled = values.get("local_ai_enabled", defaults.local_ai_enabled)
        if not isinstance(local_ai_enabled, bool):
            local_ai_enabled = defaults.local_ai_enabled
        local_ai_endpoint = values.get(
            "local_ai_endpoint", defaults.local_ai_endpoint
        )
        if not isinstance(local_ai_endpoint, str) or not local_ai_endpoint.strip():
            local_ai_endpoint = defaults.local_ai_endpoint
        local_ai_endpoint = local_ai_endpoint.strip().rstrip("/")[:500]
        if not local_ai_endpoint.startswith(("http://", "https://")):
            local_ai_endpoint = defaults.local_ai_endpoint
        local_ai_model = values.get("local_ai_model", defaults.local_ai_model)
        if not isinstance(local_ai_model, str) or not local_ai_model.strip():
            local_ai_model = defaults.local_ai_model
        local_ai_model = local_ai_model.strip()[:200]
        viewer_memory_enabled = values.get(
            "ai_viewer_memory_enabled", defaults.ai_viewer_memory_enabled
        )
        if not isinstance(viewer_memory_enabled, bool):
            viewer_memory_enabled = defaults.ai_viewer_memory_enabled
        memory_reasoning_enabled = values.get(
            "ai_memory_reasoning_enabled",
            defaults.ai_memory_reasoning_enabled,
        )
        if not isinstance(memory_reasoning_enabled, bool):
            memory_reasoning_enabled = defaults.ai_memory_reasoning_enabled
        memory_message_threshold = values.get(
            "ai_memory_message_threshold",
            defaults.ai_memory_message_threshold,
        )
        if not isinstance(memory_message_threshold, int) or isinstance(
            memory_message_threshold, bool
        ):
            memory_message_threshold = defaults.ai_memory_message_threshold
        memory_message_threshold = min(max(memory_message_threshold, 5), 50)
        memory_reset_hour = values.get(
            "ai_memory_reset_hour", defaults.ai_memory_reset_hour
        )
        if not isinstance(memory_reset_hour, int) or isinstance(memory_reset_hour, bool):
            memory_reset_hour = defaults.ai_memory_reset_hour
        memory_reset_hour = min(max(memory_reset_hour, 0), 23)
        memory_reset_minute = values.get(
            "ai_memory_reset_minute", defaults.ai_memory_reset_minute
        )
        if not isinstance(memory_reset_minute, int) or isinstance(memory_reset_minute, bool):
            memory_reset_minute = defaults.ai_memory_reset_minute
        memory_reset_minute = min(max(memory_reset_minute, 0), 59)
        memory_promo_enabled = values.get(
            "ai_memory_promo_enabled", defaults.ai_memory_promo_enabled
        )
        if not isinstance(memory_promo_enabled, bool):
            memory_promo_enabled = defaults.ai_memory_promo_enabled
        memory_promo_interval = values.get(
            "ai_memory_promo_interval_messages",
            defaults.ai_memory_promo_interval_messages,
        )
        if not isinstance(memory_promo_interval, int) or isinstance(
            memory_promo_interval, bool
        ):
            memory_promo_interval = defaults.ai_memory_promo_interval_messages
        memory_promo_interval = min(max(memory_promo_interval, 25), 1000)
        response_decisions_enabled = values.get(
            "ai_response_decisions_enabled",
            defaults.ai_response_decisions_enabled,
        )
        if not isinstance(response_decisions_enabled, bool):
            response_decisions_enabled = defaults.ai_response_decisions_enabled
        auto_send_replies = values.get(
            "ai_auto_send_replies",
            defaults.ai_auto_send_replies,
        )
        if not isinstance(auto_send_replies, bool):
            auto_send_replies = defaults.ai_auto_send_replies
        response_max_age = values.get(
            "ai_response_max_age_seconds",
            defaults.ai_response_max_age_seconds,
        )
        if not isinstance(response_max_age, int) or isinstance(
            response_max_age, bool
        ):
            response_max_age = defaults.ai_response_max_age_seconds
        response_max_age = min(max(response_max_age, 5), 60)
        response_min_interval = values.get(
            "ai_response_min_interval_seconds",
            defaults.ai_response_min_interval_seconds,
        )
        if not isinstance(response_min_interval, int) or isinstance(
            response_min_interval, bool
        ):
            response_min_interval = defaults.ai_response_min_interval_seconds
        response_min_interval = min(max(response_min_interval, 3), 60)
        conversation_followup = values.get(
            "ai_conversation_followup_seconds",
            defaults.ai_conversation_followup_seconds,
        )
        if not isinstance(conversation_followup, int) or isinstance(
            conversation_followup, bool
        ):
            conversation_followup = defaults.ai_conversation_followup_seconds
        conversation_followup = min(max(conversation_followup, 30), 600)
        interjections_enabled = values.get(
            "ai_interjections_enabled", defaults.ai_interjections_enabled
        )
        if not isinstance(interjections_enabled, bool):
            interjections_enabled = defaults.ai_interjections_enabled
        interjection_interval = values.get(
            "ai_interjection_min_interval_seconds",
            defaults.ai_interjection_min_interval_seconds,
        )
        if not isinstance(interjection_interval, int) or isinstance(
            interjection_interval, bool
        ):
            interjection_interval = defaults.ai_interjection_min_interval_seconds
        interjection_interval = min(max(interjection_interval, 60), 1800)
        personality = values.get("ai_personality", defaults.ai_personality)
        if not isinstance(personality, str) or not personality.strip():
            personality = defaults.ai_personality
        personality = personality.strip()[:2_000]
        allow_mild_profanity = values.get(
            "ai_allow_mild_profanity",
            defaults.ai_allow_mild_profanity,
        )
        if not isinstance(allow_mild_profanity, bool):
            allow_mild_profanity = defaults.ai_allow_mild_profanity
        allow_strong_profanity = values.get(
            "ai_allow_strong_profanity",
            defaults.ai_allow_strong_profanity,
        )
        if not isinstance(allow_strong_profanity, bool):
            allow_strong_profanity = defaults.ai_allow_strong_profanity
        if allow_strong_profanity:
            allow_mild_profanity = True

        return cls(
            startup_page=startup_page,
            log_level=log_level,
            ui_log_limit=ui_log_limit,
            show_developer_tools=show_developer_tools,
            twitch_chat_show_timestamps=show_timestamps,
            twitch_chat_font_family=font_family,
            twitch_chat_font_size=font_size,
            local_ai_enabled=local_ai_enabled,
            local_ai_endpoint=local_ai_endpoint,
            local_ai_model=local_ai_model,
            ai_viewer_memory_enabled=viewer_memory_enabled,
            ai_memory_reasoning_enabled=memory_reasoning_enabled,
            ai_memory_message_threshold=memory_message_threshold,
            ai_memory_reset_hour=memory_reset_hour,
            ai_memory_reset_minute=memory_reset_minute,
            ai_memory_promo_enabled=memory_promo_enabled,
            ai_memory_promo_interval_messages=memory_promo_interval,
            ai_response_decisions_enabled=response_decisions_enabled,
            ai_auto_send_replies=auto_send_replies,
            ai_response_max_age_seconds=response_max_age,
            ai_response_min_interval_seconds=response_min_interval,
            ai_conversation_followup_seconds=conversation_followup,
            ai_interjections_enabled=interjections_enabled,
            ai_interjection_min_interval_seconds=interjection_interval,
            ai_personality=personality,
            ai_allow_mild_profanity=allow_mild_profanity,
            ai_allow_strong_profanity=allow_strong_profanity,
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
