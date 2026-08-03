from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.tasks import TaskRegistry
from products.hub.twitch.tasks import TWITCH_TASK_LABELS, register_twitch_tasks
from products.hub.twitch.tasks import SendTwitchChatMessageTask
from products.hub.twitch.channel_information import (
    ChannelInformation,
    ChannelInformationStore,
    SocialLink,
)


class FakeTwitchService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_message(self, message, *, as_bot=True):
        self.calls.append(("message", message, as_bot))
        return True

    def send_pinned_message(self, message):
        self.calls.append(("pinned", message))
        return True, True

    def run_commercial(self, length):
        self.calls.append(("commercial", length))
        return {"message": "Ad started"}

    def snooze_next_ad(self):
        self.calls.append(("snooze",))
        return {}

    def update_stream_title(self, title):
        self.calls.append(("title", title))

    def update_stream_category(self, category):
        self.calls.append(("category", category))
        return category

    def resolve_user_id(self, reference):
        self.calls.append(("resolve", reference))
        return "42"

    def moderate_user(self, action, user_id, **values):
        self.calls.append(("moderate", action, user_id, values))
        return True

    def update_redemption_status(self, reward_id, redemption_id, status):
        self.calls.append(("redemption", reward_id, redemption_id, status))
        return {}


class TwitchTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.service = FakeTwitchService()
        self.channel_information = ChannelInformationStore(
            Path(self.temporary.name) / "channel-information.json"
        )
        self.channel_information.load()
        self.registry = TaskRegistry()
        register_twitch_tasks(
            self.registry,
            self.service,
            channel_information_provider=lambda: self.channel_information,
        )
        self.trigger = TriggerEvent(
            "event",
            "twitch",
            "eventsub",
            {
                "user": "Viewer",
                "user_id": "42",
                "message_id": "message-1",
                "reward_id": "reward-1",
                "redemption_id": "redeem-1",
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def execute(self, task_type: str, config: dict) -> bool:
        task = TaskDefinition("task", task_type, task_type, config)
        return self.registry.execute(task, self.trigger).succeeded

    def test_all_twitch_tasks_are_registered(self) -> None:
        self.assertEqual(set(TWITCH_TASK_LABELS), set(self.registry.registered_types()))

    def test_template_variables_do_not_include_value_padding(self) -> None:
        self.assertEqual(
            SendTwitchChatMessageTask.render(
                "hey {user}!",
                {"user": "  TestViewer  "},
            ),
            "hey TestViewer!",
        )

    def test_moderation_uses_trigger_user_and_message_ids(self) -> None:
        self.assertTrue(
            self.execute(
                "twitch.moderate_user",
                {
                    "action": "delete_message",
                    "user": "{user_id}",
                    "message_id": "{message_id}",
                    "duration_seconds": 600,
                    "reason": "",
                },
            )
        )
        self.assertIn(("resolve", "42"), self.service.calls)
        moderation = next(call for call in self.service.calls if call[0] == "moderate")
        self.assertEqual(moderation[3]["message_id"], "message-1")

    def test_commercial_and_redemption_tasks(self) -> None:
        self.assertTrue(self.execute("twitch.run_commercial", {"length": 90}))
        self.assertTrue(
            self.execute(
                "twitch.update_redemption",
                {
                    "reward_id": "{reward_id}",
                    "redemption_id": "{redemption_id}",
                    "action": "refund",
                },
            )
        )
        self.assertIn(("commercial", 90), self.service.calls)
        self.assertIn(("redemption", "reward-1", "redeem-1", "CANCELED"), self.service.calls)

    def test_stream_title_and_category_tasks_render_variables(self) -> None:
        self.trigger.context.update({"game": "Portal 2", "user": "Viewer"})

        self.assertTrue(
            self.execute(
                "twitch.update_stream_title",
                {"title": "Playing {game} with {user}"},
            )
        )
        self.assertTrue(
            self.execute(
                "twitch.update_stream_category",
                {"category": "{game}"},
            )
        )

        self.assertIn(("title", "Playing Portal 2 with Viewer"), self.service.calls)
        self.assertIn(("category", "Portal 2"), self.service.calls)

    def test_channel_information_tasks_generate_values_and_status(self) -> None:
        information = ChannelInformation(schedule="Friday at 8 PM")
        information.social_links["discord"] = SocialLink(
            True, "https://discord.gg/example"
        )
        information.social_links["youtube"] = SocialLink(
            False, "https://youtube.com/@example"
        )
        self.channel_information.save(information)

        self.assertTrue(
            self.execute(
                "twitch.get_channel_information_field",
                {"field": "schedule"},
            )
        )
        self.assertEqual(self.trigger.context["schedule"], "Friday at 8 PM")
        self.assertEqual(self.trigger.context["schedule_status"], "available")
        self.assertEqual(self.trigger.context["channel_information_available"], "true")
        self.assertTrue(
            self.execute(
                "twitch.get_channel_information_field",
                {"field": "schedule", "output_variable": "next_stream"},
            )
        )
        self.assertEqual(self.trigger.context["next_stream"], "Friday at 8 PM")
        self.assertEqual(self.trigger.context["next_stream_status"], "available")
        self.assertTrue(
            self.execute(
                "twitch.build_social_links_message",
                {"maximum_characters": 480},
            )
        )
        self.assertEqual(
            self.trigger.context["social_links_message"],
            "Discord: https://discord.gg/example",
        )
        self.assertNotIn("YouTube", self.trigger.context["social_links_message"])

    def test_unavailable_channel_information_and_missing_templates_never_send(self) -> None:
        self.assertFalse(
            self.execute(
                "twitch.get_channel_information_field",
                {"field": "discord_url"},
            )
        )
        self.assertEqual(self.trigger.context["discord_url_status"], "unavailable")
        self.assertFalse(
            self.execute(
                "twitch.send_chat_message",
                {"message": "Join: {discord_url}", "as_bot": True},
            )
        )
        self.assertEqual(self.service.calls, [])
        checked_blank = ChannelInformation()
        checked_blank.social_links["discord"] = SocialLink(True, "")
        self.channel_information.save(checked_blank)
        self.assertFalse(
            self.execute(
                "twitch.build_social_links_message",
                {"maximum_characters": 480},
            )
        )
        self.assertEqual(
            self.trigger.context["social_links_message_status"],
            "unavailable",
        )

if __name__ == "__main__":
    unittest.main()
