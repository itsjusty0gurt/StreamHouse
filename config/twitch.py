"""Public Twitch application configuration for Sally AI Bot."""

TWITCH_CLIENT_ID = "5vcq2b7cedxv7moa92pbf0gg0f74v2"
TWITCH_SCOPES = (
    "user:read:chat",
    "user:write:chat",
    "moderator:read:chatters",
    "moderator:read:followers",
    "moderation:read",
    "moderator:read:vips",
    "channel:read:subscriptions",
    "channel:read:ads",
    "channel:manage:ads",
    "channel:edit:commercial",
    "bits:read",
    "channel:read:redemptions",
)

TWITCH_COMPANION_SCOPES = frozenset(
    {
        "moderator:read:chatters",
        "moderator:read:followers",
        "moderation:read",
        "moderator:read:vips",
        "channel:read:subscriptions",
    }
)
