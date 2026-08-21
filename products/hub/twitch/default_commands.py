from __future__ import annotations

from dataclasses import dataclass

from products.hub.automation.models import TaskDefinition


DEFAULT_COMMAND_MANAGED_KEY = "twitch.command"


@dataclass(frozen=True, slots=True)
class DefaultCommandDefinition:
    default_id: str
    name: str
    tasks: tuple[TaskDefinition, ...]
    global_cooldown_seconds: int = 10
    user_cooldown_seconds: int = 30
    enabled: bool = True
    setup_requirement: str = ""

    @property
    def trigger_id(self) -> str:
        return f"streamhouse.default.command.{self.default_id}"

    @property
    def routine_id(self) -> str:
        return f"streamhouse.default.routine.{self.default_id}"


def _task(command: str, key: str, task_type: str, name: str, config: dict):
    return TaskDefinition(
        task_id=f"streamhouse.default.task.{command}.{key}",
        task_type=task_type,
        name=name,
        config=config,
        managed_key=(
            DEFAULT_COMMAND_MANAGED_KEY
            if task_type == "twitch.send_chat_message"
            else ""
        ),
    )


def _select(command: str, selector: str, cases: dict[str, str]):
    return _task(
        command,
        "response",
        "core.select_text",
        "Choose command response",
        {
            "selector": selector,
            "cases": cases,
            "default": cases.get("error", "I couldn't retrieve that information right now."),
            "output_variable": "command_response",
        },
    )


def _send(command: str):
    return _task(
        command,
        "send",
        "twitch.send_chat_message",
        "Send Twitch chat response",
        {"message": "{automation.command_response}", "as_bot": True},
    )


def _channel_information_command(
    command: str,
    field: str,
    response: str,
) -> DefaultCommandDefinition:
    return DefaultCommandDefinition(
        command,
        command,
        (
            _task(
                command,
                "channel-information",
                "twitch.get_channel_information_field",
                f"Get {field.replace('_', ' ').title()}",
                {"field": field},
            ),
            _task(
                command,
                "send",
                "twitch.send_chat_message",
                "Send Twitch chat response",
                {"message": response, "as_bot": True},
            ),
        ),
        global_cooldown_seconds=15,
        user_cooldown_seconds=30,
        enabled=False,
        setup_requirement=field,
    )


def default_command_definitions() -> tuple[DefaultCommandDefinition, ...]:
    uptime = DefaultCommandDefinition(
        "uptime",
        "uptime",
        (
            _task("uptime", "stream", "twitch.get_stream_information", "Get stream information", {}),
            _task(
                "uptime",
                "duration",
                "core.format_duration",
                "Format stream uptime",
                {"start": "{automation.stream_started_at}", "end": "", "seconds": "", "output_variable": "uptime"},
            ),
            _select(
                "uptime",
                "{automation.stream_status}:{automation.uptime_status}",
                {
                    "live:available": "The stream has been live for {automation.uptime}.",
                    "offline:missing": "The channel is currently offline.",
                    "error:missing": "I couldn't retrieve the stream status right now.",
                    "error": "I couldn't retrieve the stream status right now.",
                },
            ),
            _send("uptime"),
        ),
    )
    followage = DefaultCommandDefinition(
        "followage",
        "followage",
        (
            _task("followage", "user", "twitch.resolve_user", "Resolve target user", {"reference": "{command.target}"}),
            _task("followage", "follow", "twitch.get_follow_relationship", "Get follow relationship", {"user_id": "{automation.target_user_id}"}),
            _task(
                "followage",
                "duration",
                "core.format_duration",
                "Format follow age",
                {"start": "{automation.followed_at}", "end": "", "seconds": "", "output_variable": "followage"},
            ),
            _select(
                "followage",
                "{automation.follow_status}:{automation.followage_status}",
                {
                    "following:available": "{automation.target_display_name} has followed {automation.channel_display_name} for {automation.followage}.",
                    "not_following:missing": "{automation.target_display_name} is not currently following {automation.channel_display_name}.",
                    "broadcaster:missing": "{automation.target_display_name} is the broadcaster for {automation.channel_display_name}.",
                    "user_not_found:missing": "I couldn't find that Twitch user.",
                    "missing_scope:missing": "Follow information is unavailable because the required Twitch permission has not been granted.",
                    "error:missing": "I couldn't retrieve follow information right now.",
                    "error": "I couldn't retrieve follow information right now.",
                },
            ),
            _send("followage"),
        ),
        global_cooldown_seconds=5,
        user_cooldown_seconds=15,
    )
    accountage = DefaultCommandDefinition(
        "accountage",
        "accountage",
        (
            _task("accountage", "user", "twitch.resolve_user", "Resolve target user", {"reference": "{command.target}"}),
            _task(
                "accountage",
                "duration",
                "core.format_duration",
                "Format account age",
                {"start": "{automation.account_created_at}", "end": "", "seconds": "", "output_variable": "account_age"},
            ),
            _select(
                "accountage",
                "{automation.user_lookup_status}:{automation.account_age_status}",
                {
                    "found:available": "{automation.target_display_name}'s Twitch account was created {automation.account_age} ago.",
                    "not_found:missing": "I couldn't find that Twitch user.",
                    "error:missing": "I couldn't retrieve that Twitch account right now.",
                    "error": "I couldn't retrieve that Twitch account right now.",
                },
            ),
            _send("accountage"),
        ),
        global_cooldown_seconds=5,
        user_cooldown_seconds=15,
    )
    title = DefaultCommandDefinition(
        "title",
        "title",
        (
            _task("title", "channel", "twitch.get_channel_information", "Get channel information", {}),
            _select(
                "title",
                "{automation.title_status}",
                {
                    "available": "Current title: {automation.stream_title}",
                    "unavailable": "No Twitch stream title is currently set.",
                    "error": "I couldn't retrieve the stream title right now.",
                },
            ),
            _send("title"),
        ),
    )
    game = DefaultCommandDefinition(
        "game",
        "game",
        (
            _task("game", "channel", "twitch.get_channel_information", "Get channel information", {}),
            _select(
                "game",
                "{automation.category_status}",
                {
                    "set": "We're currently streaming {automation.stream_category}.",
                    "unset": "No Twitch category is currently set.",
                    "error": "I couldn't retrieve the Twitch category right now.",
                },
            ),
            _send("game"),
        ),
    )
    commands = DefaultCommandDefinition(
        "commands",
        "commands",
        (
            _task(
                "commands",
                "list",
                "twitch.build_command_list",
                "Build enabled command list",
                {"maximum_characters": 440},
            ),
            _select(
                "commands",
                "{automation.command_list_status}",
                {
                    "available": "Commands: {automation.command_list}",
                    "empty": "No chat commands are currently enabled.",
                    "error": "I couldn't build the command list right now.",
                },
            ),
            _send("commands"),
        ),
        global_cooldown_seconds=10,
        user_cooldown_seconds=30,
    )
    discord = _channel_information_command(
        "discord", "discord_url", "Join the Discord: {automation.discord_url}"
    )
    socials = DefaultCommandDefinition(
        "socials",
        "socials",
        (
            _task(
                "socials",
                "social-links",
                "twitch.build_social_links_message",
                "Build social links message",
                {"maximum_characters": 480},
            ),
            _task(
                "socials",
                "send",
                "twitch.send_chat_message",
                "Send Twitch chat response",
                {"message": "{automation.social_links_message}", "as_bot": True},
            ),
        ),
        global_cooldown_seconds=15,
        user_cooldown_seconds=30,
        enabled=False,
        setup_requirement="socials",
    )
    youtube = _channel_information_command(
        "youtube", "youtube_url", "YouTube: {automation.youtube_url}"
    )
    schedule = _channel_information_command(
        "schedule", "schedule", "Schedule: {automation.schedule}"
    )
    rules = _channel_information_command(
        "rules", "rules", "Channel rules: {automation.rules}"
    )
    server = _channel_information_command(
        "server", "server_info", "Server information: {automation.server_info}"
    )
    return (
        uptime,
        followage,
        accountage,
        title,
        game,
        commands,
        discord,
        socials,
        youtube,
        schedule,
        rules,
        server,
    )


def default_command_order() -> dict[str, int]:
    return {
        definition.default_id: index
        for index, definition in enumerate(default_command_definitions())
    }
