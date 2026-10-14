from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlsplit

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

    def send_shoutout(self, target):
        self.calls.append(("shoutout", target))
        return "42"

    def get_user_clips(self, target, *, featured_only=False):
        self.calls.append(("clips", target, featured_only))
        return [
            {
                "id": "older-popular",
                "url": "https://clips.twitch.tv/older-popular",
                "embed_url": "https://clips.twitch.tv/embed?clip=older-popular",
                "title": "Popular clip",
                "duration": 12.9,
                "thumbnail_url": "https://example.test/popular.jpg",
                "created_at": "2025-01-01T00:00:00Z",
                "view_count": 100,
                "is_featured": featured_only,
            },
            {
                "id": "newer",
                "url": "https://clips.twitch.tv/newer",
                "embed_url": "https://clips.twitch.tv/embed?clip=newer",
                "title": "New clip",
                "duration": 8.4,
                "thumbnail_url": "https://example.test/new.jpg",
                "created_at": "2026-01-01T00:00:00Z",
                "view_count": 10,
                "is_featured": featured_only,
            },
        ]

    def get_user_information(self, target):
        self.calls.append(("user_info", target))
        return (
            {
                "id": "9001",
                "login": "resolvedlogin",
                "display_name": "ResolvedLogin",
                "description": "A Twitch creator.",
                "profile_image_url": "https://example.test/avatar.png",
                "broadcaster_type": "partner",
            },
            {
                "game_id": "509658",
                "game_name": "Just Chatting",
                "title": "A current or recent stream title",
            },
        )

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
        self.ads_service = FakeTwitchService()
        self.channel_information = ChannelInformationStore(
            Path(self.temporary.name) / "channel-information.json"
        )
        self.channel_information.load()
        self.clip_media_resolver = Mock()
        self.clip_media_resolver.resolve.return_value = (
            "https://production.assets.clips.twitchcdn.net/clip.mp4?sig=test"
        )
        self.registry = TaskRegistry()
        register_twitch_tasks(
            self.registry,
            self.service,
            channel_information_provider=lambda: self.channel_information,
            ads_service=self.ads_service,
            clip_media_resolver=self.clip_media_resolver,
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
                "user.id": "42",
                "chat.message_id": "message-1",
                "event.reward_id": "reward-1",
                "event.redemption_id": "redeem-1",
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
                "hey {user.display_name}!",
                {"user.display_name": "  TestViewer  "},
            ),
            "hey TestViewer!",
        )

    def test_canonical_dotted_variables_are_valid_templates(self) -> None:
        SendTwitchChatMessageTask.validate_template(
            "{user.name}: {custom.game_mode} / {counter.deaths.total}"
        )
        self.assertEqual(
            SendTwitchChatMessageTask.render(
                "{user.name}: {custom.game_mode}",
                {"user.name": "Viewer", "custom.game_mode": "Hardcore"},
            ),
            "Viewer: Hardcore",
        )
        with self.assertRaisesRegex(
            ValueError, "Invalid canonical variable placeholder"
        ) as raised:
            SendTwitchChatMessageTask.validate_template("{random_line}")
        self.assertNotIn("command variable", str(raised.exception).casefold())

    def test_moderation_uses_trigger_user_and_message_ids(self) -> None:
        self.assertTrue(
            self.execute(
                "twitch.moderate_user",
                {
                    "action": "delete_message",
                    "user": "{user.id}",
                    "message_id": "{chat.message_id}",
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
                    "reward_id": "{event.reward_id}",
                    "redemption_id": "{event.redemption_id}",
                    "action": "refund",
                },
            )
        )
        self.assertIn(("commercial", 90), self.ads_service.calls)
        self.assertNotIn(("commercial", 90), self.service.calls)
        self.assertIn(("redemption", "reward-1", "redeem-1", "CANCELED"), self.service.calls)

    def test_stream_title_and_category_tasks_render_variables(self) -> None:
        self.trigger.context.update({"stream.category": "Portal 2", "user.display_name": "Viewer"})

        self.assertTrue(
            self.execute(
                "twitch.update_stream_title",
                {"title": "Playing {stream.category} with {user.display_name}"},
            )
        )
        self.assertTrue(
            self.execute(
                "twitch.update_stream_category",
                {"category": "{stream.category}"},
            )
        )

        self.assertIn(("title", "Playing Portal 2 with Viewer"), self.service.calls)
        self.assertIn(("category", "Portal 2"), self.service.calls)

    def test_shoutout_task_accepts_literal_and_generic_variable_targets(self) -> None:
        self.trigger.context.update(
            {
                "command.data": "  @SomeStreamer  ",
                "user.name": "TriggerViewer",
                "automation.some_output": "ResolvedViewer",
                "custom.some_value": "CustomViewer",
            }
        )
        for template, expected in (
            ("literalviewer", "literalviewer"),
            ("{command.data}", "@SomeStreamer"),
            ("{user.name}", "TriggerViewer"),
            ("{user.id}", "42"),
            ("{automation.some_output}", "ResolvedViewer"),
            ("{custom.some_value}", "CustomViewer"),
        ):
            with self.subTest(template=template):
                self.assertTrue(
                    self.execute("twitch.shoutout_user", {"target": template})
                )
                self.assertEqual(self.service.calls[-1], ("shoutout", expected))

    def test_shoutout_task_reports_empty_and_api_failures(self) -> None:
        empty = TaskDefinition(
            "empty", "twitch.shoutout_user", "Shoutout", {"target": "  "}
        )
        result = self.registry.execute(empty, self.trigger)
        self.assertFalse(result.succeeded)
        self.assertIn("Enter a Twitch user", result.detail)

        self.service.send_shoutout = Mock(
            side_effect=ValueError("Twitch could not send the shoutout: cooldown")
        )
        failed = TaskDefinition(
            "failed", "twitch.shoutout_user", "Shoutout", {"target": "viewer"}
        )
        result = self.registry.execute(failed, self.trigger)
        self.assertFalse(result.succeeded)
        self.assertIn("cooldown", result.detail)

    @patch("products.hub.twitch.tasks.choice", side_effect=lambda clips: clips[-1])
    def test_get_user_clip_supports_all_selection_modes_and_outputs(self, _choice) -> None:
        expected_ids = {
            "random": "newer",
            "random_featured": "newer",
            "recent": "newer",
            "most_viewed": "older-popular",
        }
        for mode, expected_id in expected_ids.items():
            with self.subTest(mode=mode):
                self.trigger.context.pop("automation.clip_id", None)
                task = TaskDefinition(
                    f"clip-{mode}",
                    "twitch.get_user_clip",
                    "Get clip",
                    {"target": "{user.id}", "selection_mode": mode},
                )
                result = self.registry.execute(task, self.trigger)

                self.assertTrue(result.succeeded, result.detail)
                self.assertEqual(
                    self.service.calls[-1],
                    ("clips", "42", mode == "random_featured"),
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_id"], expected_id
                )
                selected = next(
                    clip
                    for clip in self.service.get_user_clips(
                        "unused", featured_only=mode == "random_featured"
                    )
                    if clip["id"] == expected_id
                )
                self.service.calls.pop()
                self.assertEqual(
                    self.trigger.context["automation.clip_url"],
                    selected["url"],
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_embed_url"],
                    f"{selected['embed_url']}&parent=localhost&autoplay=true&muted=false",
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_media_url"],
                    "https://production.assets.clips.twitchcdn.net/clip.mp4?sig=test",
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_title"], selected["title"]
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_duration"],
                    str(selected["duration"]),
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_thumbnail"],
                    selected["thumbnail_url"],
                )

    def test_get_user_clip_normalizes_embed_query_without_duplicates(self) -> None:
        self.service.get_user_clips = Mock(
            return_value=[
                {
                    "id": "clip-1",
                    "url": "https://www.twitch.tv/example/clip/clip-1",
                    "embed_url": (
                        "https://clips.twitch.tv/embed?theme=dark&parent=old.example"
                        "&autoplay=false&muted=true&parent=second.example&clip=clip-1"
                    ),
                    "title": "Clip title",
                    "duration": 7.5,
                    "thumbnail_url": "https://example.test/clip.jpg",
                    "created_at": "2026-01-01T00:00:00Z",
                    "view_count": 1,
                }
            ]
        )

        result = self.registry.execute(
            TaskDefinition(
                "clip-query",
                "twitch.get_user_clip",
                "Get clip",
                {"target": "viewer", "selection_mode": "recent"},
            ),
            self.trigger,
        )

        self.assertTrue(result.succeeded, result.detail)
        output = self.trigger.context["automation.clip_embed_url"]
        parameters = parse_qsl(urlsplit(output).query, keep_blank_values=True)
        self.assertEqual(parameters.count(("clip", "clip-1")), 1)
        self.assertEqual(parameters.count(("parent", "localhost")), 1)
        self.assertEqual(parameters.count(("autoplay", "true")), 1)
        self.assertEqual(parameters.count(("muted", "false")), 1)
        self.assertEqual(parameters.count(("theme", "dark")), 1)
        self.assertEqual(
            len([key for key, _value in parameters if key == "parent"]), 1
        )

    def test_get_user_clip_succeeds_when_direct_media_resolution_fails(self) -> None:
        for failure in ("", RuntimeError("WebEngine unavailable")):
            with self.subTest(failure=type(failure).__name__):
                if isinstance(failure, Exception):
                    self.clip_media_resolver.resolve.side_effect = failure
                else:
                    self.clip_media_resolver.resolve.side_effect = None
                    self.clip_media_resolver.resolve.return_value = failure
                result = self.registry.execute(
                    TaskDefinition(
                        "clip-fallback",
                        "twitch.get_user_clip",
                        "Get clip",
                        {"target": "viewer", "selection_mode": "recent"},
                    ),
                    self.trigger,
                )

                self.assertTrue(result.succeeded, result.detail)
                self.assertEqual(
                    self.trigger.context["automation.clip_url"],
                    "https://clips.twitch.tv/newer",
                )
                self.assertIn(
                    "https://clips.twitch.tv/embed?",
                    self.trigger.context["automation.clip_embed_url"],
                )
                self.assertEqual(
                    self.trigger.context["automation.clip_media_url"], ""
                )

    def test_get_user_clip_clears_stale_outputs_before_failure(self) -> None:
        self.trigger.context["automation.clip_media_url"] = "https://stale.invalid/a.mp4"
        self.service.get_user_clips = Mock(side_effect=ValueError("Twitch failed"))

        result = self.registry.execute(
            TaskDefinition(
                "clip-failure",
                "twitch.get_user_clip",
                "Get clip",
                {"target": "viewer", "selection_mode": "recent"},
            ),
            self.trigger,
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(self.trigger.context["automation.clip_media_url"], "")
        self.assertEqual(self.trigger.context["automation.clip_embed_url"], "")
        self.assertEqual(self.trigger.context["automation.clip_url"], "")

    def test_get_user_clip_rejects_missing_or_invalid_embed_url(self) -> None:
        cases = (
            ("missing", "", "incomplete clip embed"),
            ("page-url", "https://www.twitch.tv/example/clip/clip-1", "invalid clip embed"),
            ("wrong-host", "https://example.test/embed?clip=clip-1", "invalid clip embed"),
            ("wrong-id", "https://clips.twitch.tv/embed?clip=other", "mismatched clip embed"),
        )
        for name, embed_url, expected in cases:
            with self.subTest(name=name):
                self.service.get_user_clips = Mock(
                    return_value=[
                        {
                            "id": "clip-1",
                            "url": "https://www.twitch.tv/example/clip/clip-1",
                            "embed_url": embed_url,
                            "title": "Clip title",
                            "duration": 7.5,
                            "thumbnail_url": "https://example.test/clip.jpg",
                            "created_at": "2026-01-01T00:00:00Z",
                            "view_count": 1,
                        }
                    ]
                )
                result = self.registry.execute(
                    TaskDefinition(
                        f"clip-{name}",
                        "twitch.get_user_clip",
                        "Get clip",
                        {"target": "viewer", "selection_mode": "recent"},
                    ),
                    self.trigger,
                )
                self.assertFalse(result.succeeded)
                self.assertIn(expected, result.detail.casefold())

    def test_get_user_clip_rejects_missing_or_invalid_share_url(self) -> None:
        cases = (
            ("missing", "", "incomplete clip information"),
            ("insecure", "http://clips.twitch.tv/clip-1", "invalid clip url"),
            ("wrong-host", "https://example.test/clip-1", "invalid clip url"),
        )
        for name, share_url, expected in cases:
            with self.subTest(name=name):
                self.service.get_user_clips = Mock(
                    return_value=[
                        {
                            "id": "clip-1",
                            "url": share_url,
                            "embed_url": "https://clips.twitch.tv/embed?clip=clip-1",
                            "title": "Clip title",
                            "duration": 7.5,
                            "thumbnail_url": "https://example.test/clip.jpg",
                            "created_at": "2026-01-01T00:00:00Z",
                            "view_count": 1,
                        }
                    ]
                )
                result = self.registry.execute(
                    TaskDefinition(
                        f"clip-share-{name}",
                        "twitch.get_user_clip",
                        "Get clip",
                        {"target": "viewer", "selection_mode": "recent"},
                    ),
                    self.trigger,
                )
                self.assertFalse(result.succeeded)
                self.assertIn(expected, result.detail.casefold())
                self.assertEqual(self.trigger.context["automation.clip_url"], "")

    def test_get_user_clip_reports_empty_results_without_fallback(self) -> None:
        self.service.get_user_clips = Mock(return_value=[])
        for mode, expected in (
            ("random", "No Twitch clips"),
            ("random_featured", "No featured Twitch clips"),
        ):
            with self.subTest(mode=mode):
                task = TaskDefinition(
                    mode,
                    "twitch.get_user_clip",
                    "Get clip",
                    {"target": "{user.id}", "selection_mode": mode},
                )
                result = self.registry.execute(task, self.trigger)
                self.assertFalse(result.succeeded)
                self.assertIn(expected, result.detail)
        self.assertEqual(
            self.service.get_user_clips.call_args_list,
            [
                unittest.mock.call("42", featured_only=False),
                unittest.mock.call("42", featured_only=True),
            ],
        )

    def test_get_user_info_supports_target_forms_and_publishes_grouped_outputs(self) -> None:
        self.trigger.context.update(
            {
                "user.login": "TriggerViewer",
                "user.display_name": "Trigger Viewer",
                "custom.lookup_target": "VariableViewer",
            }
        )
        original_user_context = {
            name: value
            for name, value in self.trigger.context.items()
            if name.startswith("user.")
        }
        for target, expected in (
            ("9001", "9001"),
            ("SomeLogin", "SomeLogin"),
            ("@SomeLogin", "@SomeLogin"),
            ("{custom.lookup_target}", "VariableViewer"),
            ("{user.id}", "42"),
        ):
            with self.subTest(target=target):
                result = self.registry.execute(
                    TaskDefinition(
                        f"info-{expected}",
                        "twitch.get_user_info",
                        "Get user info",
                        {"target": target},
                    ),
                    self.trigger,
                )
                self.assertTrue(result.succeeded, result.detail)
                self.assertEqual(self.service.calls[-1], ("user_info", expected))
                self.assertEqual(
                    {
                        name: value
                        for name, value in self.trigger.context.items()
                        if name.startswith("user.")
                    },
                    original_user_context,
                )
                self.assertEqual(self.trigger.context["automation.user.id"], "9001")
                self.assertEqual(
                    self.trigger.context["automation.user.login"], "resolvedlogin"
                )
                self.assertEqual(
                    self.trigger.context["automation.user.display_name"],
                    "ResolvedLogin",
                )
                self.assertEqual(
                    self.trigger.context["automation.user.description"],
                    "A Twitch creator.",
                )
                self.assertEqual(
                    self.trigger.context["automation.user.profile_image_url"],
                    "https://example.test/avatar.png",
                )
                self.assertEqual(
                    self.trigger.context["automation.user.broadcaster_type"], "partner"
                )
                self.assertEqual(
                    self.trigger.context["automation.user.game_id"], "509658"
                )
                self.assertEqual(
                    self.trigger.context["automation.user.game_name"], "Just Chatting"
                )
                self.assertEqual(
                    self.trigger.context["automation.user.stream_title"],
                    "A current or recent stream title",
                )
                self.assertEqual(
                    self.trigger.context["automation.user.channel_url"],
                    "https://www.twitch.tv/resolvedlogin",
                )

    def test_get_user_info_clears_outputs_on_empty_or_failed_lookup(self) -> None:
        output_names = (
            "id", "login", "display_name", "description", "profile_image_url",
            "broadcaster_type", "game_id", "game_name", "stream_title", "channel_url",
        )
        for name in output_names:
            self.trigger.context[f"automation.user.{name}"] = "stale"

        empty = self.registry.execute(
            TaskDefinition("empty-info", "twitch.get_user_info", "Info", {"target": " "}),
            self.trigger,
        )
        self.assertFalse(empty.succeeded)
        self.assertIn("Enter a Twitch user", empty.detail)
        self.assertTrue(
            all(not self.trigger.context[f"automation.user.{name}"] for name in output_names)
        )

        self.service.get_user_information = Mock(
            side_effect=ValueError('Twitch user "missing" was not found.')
        )
        failed = self.registry.execute(
            TaskDefinition(
                "failed-info",
                "twitch.get_user_info",
                "Info",
                {"target": "missing"},
            ),
            self.trigger,
        )
        self.assertFalse(failed.succeeded)
        self.assertIn("not found", failed.detail)
        self.assertEqual(self.trigger.context["automation.user.channel_url"], "")

        self.service.get_user_information = Mock(side_effect=OSError("network unavailable"))
        api_failed = self.registry.execute(
            TaskDefinition(
                "api-failed-info",
                "twitch.get_user_info",
                "Info",
                {"target": "target"},
            ),
            self.trigger,
        )
        self.assertFalse(api_failed.succeeded)
        self.assertIn("network unavailable", api_failed.detail)
        self.assertEqual(self.trigger.context["automation.user.channel_url"], "")

    def test_social_links_message_uses_selected_channel_information(self) -> None:
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
                "twitch.build_social_links_message",
                {"maximum_characters": 480},
            )
        )
        self.assertEqual(
            self.trigger.context["automation.social_links_message"],
            "Discord: https://discord.gg/example",
        )
        self.assertNotIn("YouTube", self.trigger.context["automation.social_links_message"])

    def test_unavailable_social_message_never_sends(self) -> None:
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
            self.trigger.context["automation.social_links_message_status"],
            "unavailable",
        )

if __name__ == "__main__":
    unittest.main()
