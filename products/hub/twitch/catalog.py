from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventSubSubscription:
    type: str
    version: str

    @property
    def display_name(self) -> str:
        return f"{self.type} (v{self.version})"


# Snapshot of Twitch's official EventSub subscription catalog, verified
# against the developer documentation on 2026-07-12.
EVENTSUB_SUBSCRIPTIONS = tuple(
    EventSubSubscription(type_name, version)
    for type_name, version in (
        ("automod.message.hold", "1"),
        ("automod.message.hold", "2"),
        ("automod.message.update", "1"),
        ("automod.message.update", "2"),
        ("automod.settings.update", "1"),
        ("automod.terms.update", "1"),
        ("channel.bits.use", "1"),
        ("channel.update", "2"),
        ("channel.follow", "2"),
        ("channel.ad_break.begin", "1"),
        ("channel.chat.clear", "1"),
        ("channel.chat.clear_user_messages", "1"),
        ("channel.chat.message", "1"),
        ("channel.chat.message_delete", "1"),
        ("channel.chat.notification", "1"),
        ("channel.chat_settings.update", "1"),
        ("channel.chat.user_message_hold", "1"),
        ("channel.chat.user_message_update", "1"),
        ("channel.shared_chat.begin", "1"),
        ("channel.shared_chat.update", "1"),
        ("channel.shared_chat.end", "1"),
        ("channel.subscribe", "1"),
        ("channel.subscription.end", "1"),
        ("channel.subscription.gift", "1"),
        ("channel.subscription.message", "1"),
        ("channel.cheer", "1"),
        ("channel.raid", "1"),
        ("channel.ban", "1"),
        ("channel.unban", "1"),
        ("channel.unban_request.create", "1"),
        ("channel.unban_request.resolve", "1"),
        ("channel.moderate", "1"),
        ("channel.moderate", "2"),
        ("channel.moderator.add", "1"),
        ("channel.moderator.remove", "1"),
        ("channel.guest_star_session.begin", "beta"),
        ("channel.guest_star_session.end", "beta"),
        ("channel.guest_star_guest.update", "beta"),
        ("channel.guest_star_settings.update", "beta"),
        ("channel.channel_points_automatic_reward_redemption.add", "1"),
        ("channel.channel_points_automatic_reward_redemption.add", "2"),
        ("channel.channel_points_custom_reward.add", "1"),
        ("channel.channel_points_custom_reward.update", "1"),
        ("channel.channel_points_custom_reward.remove", "1"),
        ("channel.channel_points_custom_reward_redemption.add", "1"),
        ("channel.channel_points_custom_reward_redemption.update", "1"),
        ("channel.custom_power_up_redemption.add", "1"),
        ("channel.poll.begin", "1"),
        ("channel.poll.progress", "1"),
        ("channel.poll.end", "1"),
        ("channel.prediction.begin", "1"),
        ("channel.prediction.progress", "1"),
        ("channel.prediction.lock", "1"),
        ("channel.prediction.end", "1"),
        ("channel.suspicious_user.message", "1"),
        ("channel.suspicious_user.update", "1"),
        ("channel.vip.add", "1"),
        ("channel.vip.remove", "1"),
        ("channel.warning.acknowledge", "1"),
        ("channel.warning.send", "1"),
        ("channel.charity_campaign.donate", "1"),
        ("channel.charity_campaign.start", "1"),
        ("channel.charity_campaign.progress", "1"),
        ("channel.charity_campaign.stop", "1"),
        ("conduit.shard.disabled", "1"),
        ("drop.entitlement.grant", "1"),
        ("extension.bits_transaction.create", "1"),
        ("channel.goal.begin", "1"),
        ("channel.goal.progress", "1"),
        ("channel.goal.end", "1"),
        ("channel.hype_train.begin", "2"),
        ("channel.hype_train.progress", "2"),
        ("channel.hype_train.end", "2"),
        ("channel.shield_mode.begin", "1"),
        ("channel.shield_mode.end", "1"),
        ("channel.shoutout.create", "1"),
        ("channel.shoutout.receive", "1"),
        ("stream.online", "1"),
        ("stream.offline", "1"),
        ("user.authorization.grant", "1"),
        ("user.authorization.revoke", "1"),
        ("user.update", "1"),
        ("user.whisper.message", "1"),
    )
)
