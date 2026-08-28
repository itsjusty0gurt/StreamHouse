"""Public Twitch application configuration for Streamhouse Hub."""

TWITCH_CLIENT_ID = "5vcq2b7cedxv7moa92pbf0gg0f74v2"
TWITCH_SCOPES = (
    "user:read:chat",
    "user:write:chat",
    "channel:bot",
    "moderator:read:chatters",
    "moderator:read:followers",
    "moderation:read",
    "moderator:manage:banned_users",
    "moderator:manage:chat_messages",
    "channel:read:vips",
    "channel:read:subscriptions",
    "channel:read:ads",
    "channel:manage:ads",
    "channel:edit:commercial",
    "channel:manage:broadcast",
    "bits:read",
    "channel:manage:redemptions",
)

TWITCH_BOT_SCOPES = (
    "user:read:chat",
    "user:write:chat",
    "user:bot",
)

TWITCH_AD_SCOPES = frozenset(
    {
        "channel:read:ads",
        "channel:manage:ads",
        "channel:edit:commercial",
    }
)

TWITCH_CHANNEL_SNAPSHOT_SCOPES = frozenset(
    {
        "moderator:read:chatters",
        "moderator:read:followers",
        "moderation:read",
        "channel:read:vips",
        "channel:read:subscriptions",
        "channel:read:ads",
    }
)
