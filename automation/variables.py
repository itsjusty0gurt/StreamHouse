from __future__ import annotations

import re
from typing import Iterable, Mapping


VARIABLE_INFO: dict[str, tuple[str, str]] = {
    "user": ("TestViewer", "Viewer or account display name"),
    "user_id": ("123456", "Twitch user ID"),
    "target_user_id": ("654321", "Target Twitch user ID"),
    "channel": ("samplechannel", "Broadcaster channel name"),
    "message": ("Hello Sally!", "Chat message or event text"),
    "message_id": ("message-123", "Twitch chat message ID"),
    "command": ("hello", "Chat command name without !"),
    "args": ("friend", "Text following a chat command"),
    "target": ("friend", "First command argument, without @"),
    "uses": ("3", "Number of times the command has run"),
    "event": ("Follow", "Readable trigger event name"),
    "event_type": ("channel.follow", "Raw service event type"),
    "input": ("Viewer supplied text", "Reward input or OBS input name"),
    "amount": ("100", "Event amount, viewers, bits, or reward cost"),
    "bits": ("100", "Number of cheered bits"),
    "viewers": ("12", "Viewer or raid count"),
    "tier": ("1000", "Twitch subscription tier"),
    "reward": ("Hydrate", "Channel-point reward title"),
    "reward_id": ("reward-123", "Channel-point reward ID"),
    "reward_cost": ("500", "Channel-point reward cost"),
    "redemption_id": ("redemption-123", "Channel-point redemption ID"),
    "title": ("Building Sally", "Current stream title"),
    "game": ("Science & Technology", "Current Twitch category"),
    "uptime": ("01:23:45", "Current stream uptime"),
    "followers": ("445", "Current follower count"),
    "scene": ("Gameplay", "OBS scene name"),
    "source": ("Camera", "OBS scene source name"),
    "output_state": ("OBS_WEBSOCKET_OUTPUT_STARTED", "OBS output state"),
    "enabled": ("true", "OBS source or Studio Mode state"),
    "mute": ("Not Muted", "OBS input mute state"),
    "muted": ("Not Muted", "OBS input mute state"),
    "volume_db": ("-8.0", "OBS input volume in decibels"),
    "media": ("Intro Video", "OBS media input name"),
}

TWITCH_VARIABLES = tuple(VARIABLE_INFO)
OBS_VARIABLES = (
    "event",
    "event_type",
    "scene",
    "source",
    "input",
    "output_state",
    "enabled",
    "mute",
    "muted",
    "volume_db",
    "media",
    "channel",
    "user",
    "message",
)
CORE_VARIABLES = (
    "event",
    "event_type",
    "channel",
    "user",
    "title",
    "game",
    "uptime",
    "followers",
)

TEMPLATE_PATTERN = re.compile(r"\{([a-z_]+)\}")


def sample_context(keys: Iterable[str]) -> dict[str, str]:
    requested = set(keys)
    return {
        key: sample
        for key, (sample, _description) in VARIABLE_INFO.items()
        if key in requested
    }


def render_preview(template: str, context: Mapping[str, str]) -> str:
    return TEMPLATE_PATTERN.sub(
        lambda match: str(
            context.get(match.group(1), f"{{{match.group(1)}}}")
        ).strip(),
        template,
    )
