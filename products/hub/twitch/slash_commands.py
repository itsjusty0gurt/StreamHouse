from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class TwitchSlashCommand:
    name: str
    syntax: str
    description: str
    required_scope: str
    requires_user: bool = False
    requires_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class TwitchSlashRequest:
    action: str
    user_reference: str = ""
    user_id: str = ""
    duration: int | None = None
    reason: str = ""
    message: str = ""


TWITCH_SLASH_COMMANDS = (
    TwitchSlashCommand(
        "ban", "/ban <user> [reason]", "Ban a user",
        "moderator:manage:banned_users", True, True,
    ),
    TwitchSlashCommand(
        "timeout", "/timeout <user> <duration> [reason]",
        "Temporarily timeout a user", "moderator:manage:banned_users",
        True, True,
    ),
    TwitchSlashCommand(
        "unban", "/unban <user>", "Remove a ban or timeout",
        "moderator:manage:banned_users", True, True,
    ),
    TwitchSlashCommand(
        "clear", "/clear", "Clear chat messages",
        "moderator:manage:chat_messages", False, True,
    ),
    TwitchSlashCommand(
        "slow", "/slow <seconds>", "Enable slow mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "slowoff", "/slowoff", "Disable slow mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "followers", "/followers [duration]", "Enable followers-only mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "followersoff", "/followersoff", "Disable followers-only mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "subscribers", "/subscribers", "Enable subscribers-only mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "subscribersoff", "/subscribersoff", "Disable subscribers-only mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "emoteonly", "/emoteonly", "Enable emote-only mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "emoteonlyoff", "/emoteonlyoff", "Disable emote-only mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "uniquechat", "/uniquechat", "Enable unique-chat mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "uniquechatoff", "/uniquechatoff", "Disable unique-chat mode",
        "moderator:manage:chat_settings",
    ),
    TwitchSlashCommand(
        "mod", "/mod <user>", "Add a channel moderator",
        "channel:manage:moderators", True, True,
    ),
    TwitchSlashCommand(
        "unmod", "/unmod <user>", "Remove a channel moderator",
        "channel:manage:moderators", True, True,
    ),
    TwitchSlashCommand(
        "vip", "/vip <user>", "Add a channel VIP",
        "channel:manage:vips", True, True,
    ),
    TwitchSlashCommand(
        "unvip", "/unvip <user>", "Remove a channel VIP",
        "channel:manage:vips", True, True,
    ),
    TwitchSlashCommand(
        "raid", "/raid <channel>", "Start a raid",
        "channel:manage:raids", True, True,
    ),
    TwitchSlashCommand(
        "unraid", "/unraid", "Cancel a pending raid",
        "channel:manage:raids", False, True,
    ),
    TwitchSlashCommand(
        "announce", "/announce <message>", "Send a chat announcement",
        "moderator:manage:announcements",
    ),
    TwitchSlashCommand(
        "shoutout", "/shoutout <user>", "Send a Twitch shoutout",
        "moderator:manage:shoutouts", True,
    ),
)

TWITCH_SLASH_COMMANDS_BY_NAME = {
    command.name: command for command in TWITCH_SLASH_COMMANDS
}

_DURATION_PATTERN = re.compile(r"(?P<value>\d+)(?P<unit>[smh]?)", re.IGNORECASE)
_DURATION_FACTORS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration_seconds(value: str) -> int:
    match = _DURATION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(
            "Duration must use seconds, minutes, or hours "
            "(for example 30s, 10m, or 2h)."
        )
    seconds = (
        int(match.group("value"))
        * _DURATION_FACTORS[match.group("unit").casefold()]
    )
    if seconds <= 0:
        raise ValueError("Duration must be greater than zero.")
    return seconds


def slash_command(name: str) -> TwitchSlashCommand:
    try:
        return TWITCH_SLASH_COMMANDS_BY_NAME[name.casefold()]
    except KeyError:
        raise ValueError(f"Unsupported Twitch slash command: /{name or '?'}") from None


def parse_twitch_slash_request(text: str) -> TwitchSlashRequest:
    clean = text.strip()
    command_token, separator, argument_text = clean.partition(" ")
    if not command_token.startswith("/"):
        raise ValueError("Enter a supported Twitch slash command.")
    action = command_token[1:].casefold()
    command = slash_command(action)
    arguments = argument_text.strip() if separator else ""

    if action in {
        "ban", "unban", "mod", "unmod", "vip", "unvip", "raid", "shoutout",
    }:
        user_reference, user_separator, remainder = arguments.partition(" ")
        user_reference = user_reference.lstrip("@").strip()
        if not user_reference:
            raise ValueError(f"Usage: {command.syntax}")
        remainder = remainder.strip() if user_separator else ""
        if action != "ban" and remainder:
            raise ValueError(f"Usage: {command.syntax}")
        return TwitchSlashRequest(
            action=action,
            user_reference=user_reference,
            reason=remainder[:500] if action == "ban" else "",
        )

    if action == "timeout":
        parts = arguments.split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError(f"Usage: {command.syntax}")
        user_reference = parts[0].lstrip("@").strip()
        if not user_reference:
            raise ValueError(f"Usage: {command.syntax}")
        duration = parse_duration_seconds(parts[1])
        if duration > 1_209_600:
            raise ValueError(
                "Timeout duration must not exceed 1,209,600 seconds."
            )
        return TwitchSlashRequest(
            action=action,
            user_reference=user_reference,
            duration=duration,
            reason=parts[2][:500] if len(parts) == 3 else "",
        )

    if action == "slow":
        if not arguments or " " in arguments:
            raise ValueError(f"Usage: {command.syntax}")
        duration = parse_duration_seconds(arguments)
        if not 3 <= duration <= 120:
            raise ValueError("Slow mode must be between 3 and 120 seconds.")
        return TwitchSlashRequest(action=action, duration=duration)

    if action == "followers":
        if not arguments:
            return TwitchSlashRequest(action=action, duration=0)
        if " " in arguments:
            raise ValueError(f"Usage: {command.syntax}")
        if arguments.isdigit():
            minutes = int(arguments)
        else:
            seconds = parse_duration_seconds(arguments)
            if seconds % 60:
                raise ValueError("Followers-only duration must resolve to whole minutes.")
            minutes = seconds // 60
        if not 0 <= minutes <= 129_600:
            raise ValueError(
                "Followers-only duration must be between 0 and 129,600 minutes."
            )
        return TwitchSlashRequest(action=action, duration=minutes)

    if action == "announce":
        if not arguments:
            raise ValueError(f"Usage: {command.syntax}")
        return TwitchSlashRequest(action=action, message=arguments[:500])

    if arguments:
        raise ValueError(f"Usage: {command.syntax}")
    return TwitchSlashRequest(action=action)
