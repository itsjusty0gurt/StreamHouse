from __future__ import annotations

from dataclasses import dataclass

from twitch.models import TwitchEvent


@dataclass(frozen=True, slots=True)
class TwitchActivityEntry:
    category: str
    text: str
    color: str


def format_twitch_activity(
    twitch_event: TwitchEvent,
) -> TwitchActivityEntry | None:
    event = twitch_event.payload.get("event", {})
    if not isinstance(event, dict):
        return None
    event_type = twitch_event.subscription_type
    user = str(
        event.get("user_name")
        or event.get("from_broadcaster_user_name")
        or event.get("chatter_user_name")
        or "Someone"
    )
    if event_type == "channel.follow":
        return TwitchActivityEntry("Follows", f"{user} followed the channel", "#bf94ff")
    if event_type == "channel.subscribe":
        return TwitchActivityEntry("Subscriptions", f"{user} subscribed", "#ff75e6")
    if event_type == "channel.subscription.gift":
        total = int(event.get("total", 1))
        return TwitchActivityEntry(
            "Subscriptions", f"{user} gifted {total} subscription(s)", "#ff75e6"
        )
    if event_type == "channel.subscription.message":
        return TwitchActivityEntry("Subscriptions", f"{user} resubscribed", "#ff75e6")
    if event_type == "channel.raid":
        viewers = int(event.get("viewers", 0))
        return TwitchActivityEntry(
            "Raids", f"{user} raided with {viewers:,} viewers", "#f5c542"
        )
    if event_type == "channel.cheer":
        bits = int(event.get("bits", 0))
        return TwitchActivityEntry("Cheers", f"{user} cheered {bits:,} bits", "#5cafff")
    if event_type == "channel.channel_points_custom_reward_redemption.add":
        reward = event.get("reward", {})
        title = (
            str(reward.get("title", "a channel reward"))
            if isinstance(reward, dict)
            else "a channel reward"
        )
        return TwitchActivityEntry("Rewards", f"{user} redeemed {title}", "#00c7ac")
    return None
