import json
import tempfile
import unittest
from pathlib import Path

from products.hub.core.settings import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_path = (
            Path(self.temporary_directory.name) / "settings.json"
        )
        self.store = SettingsStore(self.settings_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_file_returns_defaults(self) -> None:
        self.assertEqual(self.store.load(), AppSettings())

    def test_settings_round_trip(self) -> None:
        expected = AppSettings(
            startup_page="Logs",
            log_level="WARNING",
            ui_log_limit=750,
            show_developer_tools=False,
            twitch_chat_show_timestamps=False,
            twitch_chat_font_family="Consolas",
            twitch_chat_font_size=13,
            twitch_last_ad_duration=90,
            local_ai_enabled=True,
            streamhouse_ai_endpoint="http://localhost:8765",
            local_ai_endpoint="http://localhost:11434",
            local_ai_model="qwen3:14b",
            ai_viewer_memory_enabled=True,
            ai_memory_reasoning_enabled=False,
            ai_memory_message_threshold=15,
            ai_memory_reset_hour=3,
            ai_memory_reset_minute=30,
            ai_memory_promo_enabled=False,
            ai_memory_promo_interval_messages=250,
            ai_response_decisions_enabled=False,
            ai_auto_send_replies=True,
            ai_response_max_age_seconds=25,
            ai_response_min_interval_seconds=12,
            ai_conversation_followup_seconds=240,
            ai_interjections_enabled=False,
            ai_interjection_min_interval_seconds=300,
            ai_interjection_min_messages=12,
            ai_training_capture_enabled=True,
            ai_training_notice_enabled=False,
            ai_training_notice_message="Custom opt-in notice",
            ai_training_notice_stream_id="stream-42",
            ai_personality="Dry, playful, and concise.",
            ai_allow_mild_profanity=True,
            ai_allow_strong_profanity=True,
        )

        self.store.save(expected)

        self.assertEqual(self.store.load(), expected)
        self.assertFalse(self.settings_path.with_suffix(".tmp").exists())

    def test_invalid_values_fall_back_or_are_clamped(self) -> None:
        self.settings_path.write_text(
            json.dumps(
                {
                    "_version": SettingsStore.VERSION,
                    "startup_page": "Unknown",
                    "log_level": "NOISY",
                    "ui_log_limit": 99_999,
                    "show_developer_tools": "yes",
                    "twitch_chat_show_timestamps": "yes",
                    "twitch_chat_font_family": "",
                    "twitch_chat_font_size": 100,
                    "twitch_last_ad_duration": 45,
                    "local_ai_enabled": "yes",
                    "streamhouse_ai_endpoint": "not-a-url",
                    "local_ai_endpoint": "not-a-url",
                    "local_ai_model": "",
                    "ai_viewer_memory_enabled": "yes",
                    "ai_memory_reasoning_enabled": "yes",
                    "ai_memory_message_threshold": 500,
                    "ai_memory_reset_hour": 99,
                    "ai_memory_reset_minute": -10,
                    "ai_memory_promo_enabled": "yes",
                    "ai_memory_promo_interval_messages": 5000,
                    "ai_response_decisions_enabled": "yes",
                    "ai_auto_send_replies": "yes",
                    "ai_response_max_age_seconds": 500,
                    "ai_response_min_interval_seconds": 0,
                    "ai_conversation_followup_seconds": 5000,
                    "ai_interjections_enabled": "yes",
                    "ai_interjection_min_interval_seconds": 5000,
                    "ai_interjection_min_messages": 500,
                    "ai_training_capture_enabled": "yes",
                    "ai_training_notice_enabled": "yes",
                    "ai_training_notice_message": "",
                    "ai_training_notice_stream_id": 42,
                    "ai_personality": "",
                    "ai_allow_mild_profanity": "yes",
                    "ai_allow_strong_profanity": "yes",
                }
            ),
            encoding="utf-8",
        )

        settings = self.store.load()

        self.assertEqual(settings.startup_page, "Dashboard")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.ui_log_limit, 10_000)
        self.assertTrue(settings.show_developer_tools)
        self.assertTrue(settings.twitch_chat_show_timestamps)
        self.assertEqual(settings.twitch_chat_font_family, "Segoe UI")
        self.assertEqual(settings.twitch_chat_font_size, 24)
        self.assertEqual(settings.twitch_last_ad_duration, 30)
        self.assertTrue(settings.local_ai_enabled)
        self.assertEqual(settings.streamhouse_ai_endpoint, "http://127.0.0.1:8765")
        self.assertEqual(settings.local_ai_endpoint, "http://127.0.0.1:11434")
        self.assertEqual(settings.local_ai_model, "qwen3:14b")
        self.assertFalse(settings.ai_viewer_memory_enabled)
        self.assertTrue(settings.ai_memory_reasoning_enabled)
        self.assertEqual(settings.ai_memory_message_threshold, 50)
        self.assertEqual(settings.ai_memory_reset_hour, 23)
        self.assertEqual(settings.ai_memory_reset_minute, 0)
        self.assertTrue(settings.ai_memory_promo_enabled)
        self.assertEqual(settings.ai_memory_promo_interval_messages, 1000)
        self.assertTrue(settings.ai_response_decisions_enabled)
        self.assertTrue(settings.ai_auto_send_replies)
        self.assertEqual(settings.ai_response_max_age_seconds, 60)
        self.assertEqual(settings.ai_response_min_interval_seconds, 3)
        self.assertEqual(settings.ai_conversation_followup_seconds, 600)
        self.assertFalse(settings.ai_interjections_enabled)
        self.assertEqual(settings.ai_interjection_min_interval_seconds, 1800)
        self.assertFalse(settings.ai_training_capture_enabled)
        self.assertTrue(settings.ai_training_notice_enabled)
        self.assertIn(
            "Participation is optional",
            settings.ai_training_notice_message,
        )
        self.assertEqual(settings.ai_training_notice_stream_id, "")
        self.assertEqual(settings.ai_interjection_min_messages, 50)
        self.assertIn("quick-witted", settings.ai_personality)
        self.assertFalse(settings.ai_allow_mild_profanity)
        self.assertFalse(settings.ai_allow_strong_profanity)

    def test_strong_language_permission_also_enables_mild_language(self) -> None:
        settings = AppSettings.from_dict(
            {
                "ai_allow_mild_profanity": False,
                "ai_allow_strong_profanity": True,
            }
        )

        self.assertTrue(settings.ai_allow_mild_profanity)
        self.assertTrue(settings.ai_allow_strong_profanity)

    def test_non_object_json_is_rejected(self) -> None:
        self.settings_path.write_text("[]", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.store.load()

    def test_discarded_pre_alpha_schema_is_rejected(self) -> None:
        self.settings_path.write_text(
            json.dumps({"_version": 1, "startup_page": "Memories"}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "discarded pre-alpha schema"):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
