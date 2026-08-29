from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from products.hub.automation.models import TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_providers import (
    ChannelInformationVariableProvider,
    context_provider,
    runtime_provider,
)
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.automation.value_tasks import register_value_tasks
from products.hub.twitch.commands import (
    TwitchCommandTriggerStore, TwitchCommandTriggerDispatcher, TwitchCommandTriggerOutcome,
)
from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.default_commands import default_command_definitions
from products.hub.twitch.channel_information import (
    ChannelInformation,
    ChannelInformationStore,
    SocialLink,
)
from products.hub.twitch.tasks import register_twitch_tasks


class FakeTwitchInformationService:
    broadcaster_user_id = "1"

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.stream: dict | None | Exception = None
        self.channel: dict | None | Exception = {
            "title": "Building Streamhouse",
            "game_name": "Science & Technology",
            "game_id": "509670",
        }
        now = datetime.now(timezone.utc)
        self.users = {
            "1": {"id": "1", "login": "broadcaster", "display_name": "Broadcaster", "created_at": (now - timedelta(days=2000)).isoformat()},
            "2": {"id": "2", "login": "testviewer", "display_name": "TestViewer", "created_at": (now - timedelta(days=400)).isoformat()},
            "targetviewer": {"id": "3", "login": "targetviewer", "display_name": "TargetViewer", "created_at": (now - timedelta(days=800)).isoformat()},
        }
        self.follow: dict | None | Exception = {
            "followed_at": (now - timedelta(days=130)).isoformat()
        }
        self.user_error: Exception | None = None

    def send_message(self, message: str, *, as_bot: bool = True) -> bool:
        self.messages.append(message)
        return True

    def resolve_user(self, reference: str) -> dict:
        if self.user_error is not None:
            raise self.user_error
        value = self.users.get(reference.casefold()) or self.users.get(reference)
        if value is None:
            raise ValueError(f'Twitch user "{reference}" was not found.')
        return value

    def get_stream_information(self):
        if isinstance(self.stream, Exception):
            raise self.stream
        return self.stream

    def get_channel_information(self):
        if isinstance(self.channel, Exception):
            raise self.channel
        return self.channel

    def get_follow_relationship(self, _user_id: str):
        if isinstance(self.follow, Exception):
            raise self.follow
        return self.follow

    def channel_display_name(self) -> str:
        return "Broadcaster"


class DefaultTwitchCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.routines = RoutineStore(root / "routines.json")
        self.store = TwitchCommandTriggerStore(root / "commands.json", self.routines)
        for definition in default_command_definitions():
            self.store.configure_default(definition.default_id)
        self.channel_information = ChannelInformationStore(
            root / "channel-information.json"
        )
        self.channel_information.load()
        self.twitch = FakeTwitchInformationService()
        self.variable_registry = VariableRegistry()
        self.variable_registry.register(context_provider())
        self.variable_registry.register(
            runtime_provider(
                lambda: {
                    "title": str((self.twitch.channel or {}).get("title", "")),
                    "category": str((self.twitch.channel or {}).get("game_name", "")),
                    "connected": True,
                },
                obs_connected=lambda: False,
                obs_scene=lambda: "",
                hub_uptime=lambda: "1 minute",
            )
        )
        self.variable_registry.register(
            ChannelInformationVariableProvider(self.channel_information)
        )
        self.registry = TaskRegistry()
        register_twitch_tasks(
            self.registry,
            self.twitch,
            command_provider=lambda: self.store,
            channel_information_provider=lambda: self.channel_information,
            variable_registry=self.variable_registry,
        )
        register_value_tasks(self.registry)
        self.automation = AutomationService(
            self.routines, self.registry, variable_registry=self.variable_registry
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(
        self,
        name: str,
        *,
        target: str = "--",
        user_id: str = "2",
        permission: str = "everyone",
    ) -> str:
        self.twitch.messages.clear()
        command = self.store.resolve(name)
        result = self.automation.publish_trigger(
            TriggerEvent(
                command.trigger_id,
                "twitch",
                "command",
                {
                    "user": "TestViewer",
                    "user_id": user_id,
                    "user_login": "testviewer",
                    "target": target,
                    "viewer_permission": permission,
                },
            )
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(len(self.twitch.messages), 1)
        return self.twitch.messages[0]

    def test_uptime_live_offline_and_api_failure(self) -> None:
        self.twitch.stream = {
            "id": "stream-1",
            "started_at": (datetime.now(timezone.utc) - timedelta(hours=2, minutes=18)).isoformat(),
            "title": "Live",
            "game_name": "Games",
            "game_id": "10",
            "viewer_count": 12,
        }
        self.assertIn("2 hours 18 minutes", self.run_command("uptime"))
        self.twitch.stream = None
        self.assertEqual(self.run_command("uptime"), "The channel is currently offline.")
        self.twitch.stream = OSError("offline API")
        self.assertEqual(
            self.run_command("uptime"),
            "I couldn't retrieve the stream status right now.",
        )

    def test_followage_self_target_and_failure_outcomes(self) -> None:
        self.assertIn("TestViewer has followed Broadcaster", self.run_command("followage"))
        self.assertIn(
            "TargetViewer has followed Broadcaster",
            self.run_command("followage", target="@targetviewer"),
        )
        self.twitch.follow = None
        self.assertEqual(
            self.run_command("followage", target="targetviewer"),
            "TargetViewer is not currently following Broadcaster.",
        )
        self.assertEqual(
            self.run_command("followage", target="missing"),
            "I couldn't find that Twitch user.",
        )
        self.twitch.follow = PermissionError("scope")
        self.assertIn(
            "required Twitch permission",
            self.run_command("followage", target="targetviewer"),
        )
        self.twitch.follow = OSError("network")
        self.assertEqual(
            self.run_command("followage", target="targetviewer"),
            "I couldn't retrieve follow information right now.",
        )
        self.twitch.follow = None
        self.twitch.user_error = OSError("user API")
        self.assertEqual(
            self.run_command("followage", target="targetviewer"),
            "I couldn't retrieve follow information right now.",
        )

    def test_followage_broadcaster_is_handled_sensibly(self) -> None:
        self.assertEqual(
            self.run_command("followage", user_id="1"),
            "Broadcaster is the broadcaster for Broadcaster.",
        )

    def test_accountage_self_and_target(self) -> None:
        self.assertIn("TestViewer's Twitch account was created", self.run_command("accountage"))
        self.assertIn(
            "TargetViewer's Twitch account was created",
            self.run_command("accountage", target="targetviewer"),
        )

    def test_title_and_game_use_cached_stream_variables(self) -> None:
        self.assertEqual(self.run_command("title"), "Current title: Building Streamhouse")
        self.assertEqual(self.run_command("game"), "We're currently streaming Science & Technology.")
        self.twitch.channel = {"title": "Offline title", "game_name": "", "game_id": ""}
        self.assertEqual(self.run_command("title"), "Current title: Offline title")
        command = self.store.resolve("game")
        result = self.automation.publish_trigger(
            TriggerEvent(command.trigger_id, "twitch", "command", {"user": "TestViewer"})
        )
        self.assertFalse(result.succeeded)

    def test_commands_excludes_disabled_and_respects_permissions(self) -> None:
        game = self.store.resolve("game")
        self.store.set_enabled(game.trigger_id, False)
        followage = self.store.resolve("followage")
        self.store.update(
            followage.trigger_id,
            name=followage.name,
            response=self.store.response_for(followage),
            aliases=followage.aliases,
            permission="moderator",
            global_cooldown_seconds=followage.global_cooldown_seconds,
            user_cooldown_seconds=followage.user_cooldown_seconds,
        )
        response = self.run_command("commands")
        self.assertNotIn("!game", response)
        self.assertNotIn("!followage", response)
        self.assertEqual(response.count("!commands"), 1)

    def enable(self, name: str) -> None:
        command = self.store.resolve(name)
        self.store.set_enabled(command.trigger_id, True)

    def test_channel_information_commands_use_configured_values(self) -> None:
        information = ChannelInformation(
            schedule="Tuesday and Thursday at 7 PM",
            rules="Be kind. No spoilers.",
            server_info="Example Realm — play.example.com",
        )
        information.social_links["discord"] = SocialLink(
            True, "https://discord.gg/example"
        )
        information.social_links["youtube"] = SocialLink(
            True, "https://youtube.com/@example"
        )
        information.social_links["tiktok"] = SocialLink(
            False, "https://tiktok.com/@example"
        )
        self.channel_information.save(information)
        for name in ("discord", "youtube", "socials", "schedule", "rules", "server"):
            self.enable(name)

        self.assertEqual(
            self.run_command("discord"),
            "Join the Discord: https://discord.gg/example",
        )
        self.assertEqual(
            self.run_command("youtube"),
            "YouTube: https://youtube.com/@example",
        )
        socials = self.run_command("socials")
        self.assertIn("Discord: https://discord.gg/example", socials)
        self.assertIn("YouTube: https://youtube.com/@example", socials)
        self.assertNotIn("TikTok", socials)
        self.assertEqual(
            self.run_command("schedule"),
            "Schedule: Tuesday and Thursday at 7 PM",
        )
        self.assertEqual(
            self.run_command("rules"),
            "Channel rules: Be kind. No spoilers.",
        )
        self.assertEqual(
            self.run_command("server"),
            "Server information: Example Realm — play.example.com",
        )

    def test_missing_channel_information_stops_before_chat_send(self) -> None:
        self.enable("discord")
        command = self.store.resolve("discord")

        dispatcher = TwitchCommandTriggerDispatcher(
            self.store, channel_information=self.channel_information
        )
        result = dispatcher.evaluate(TwitchMessage(
            username="TestViewer", text="!discord", received_at=datetime.now(timezone.utc),
            user_id="2", user_login="testviewer",
        ))
        self.assertEqual(result.outcome, TwitchCommandTriggerOutcome.CONFIGURATION_ERROR)
        self.assertEqual(self.twitch.messages, [])


if __name__ == "__main__":
    unittest.main()
