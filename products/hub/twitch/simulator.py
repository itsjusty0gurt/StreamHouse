from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from uuid import uuid4


def create_chat_notification(
    channel: str,
    username: str,
    text: str,
) -> dict:
    """Build a realistic channel.chat.message EventSub notification."""

    user_key = username.lower().replace(" ", "_")
    fragment = {
        "type": "text",
        "text": text,
        "cheermote": None,
        "emote": None,
        "mention": None,
    }
    return {
        "subscription": {
            "id": str(uuid4()),
            "status": "enabled",
            "type": "channel.chat.message",
            "version": "1",
            "condition": {
                "broadcaster_user_id": "1000",
                "user_id": "2000",
            },
            "transport": {
                "method": "webhook",
                "callback": "http://127.0.0.1/eventsub",
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cost": 0,
        },
        "event": {
            "broadcaster_user_id": "1000",
            "broadcaster_user_login": channel,
            "broadcaster_user_name": channel,
            "chatter_user_id": "3000",
            "chatter_user_login": user_key,
            "chatter_user_name": username,
            "message_id": str(uuid4()),
            "message": {"text": text, "fragments": [fragment]},
            "color": "#BF94FF",
            "badges": [],
            "message_type": "text",
            "cheer": None,
            "reply": None,
            "channel_points_custom_reward_id": None,
            "source_broadcaster_user_id": None,
            "source_broadcaster_user_login": None,
            "source_broadcaster_user_name": None,
            "source_message_id": None,
            "source_badges": None,
            "is_source_only": False,
        },
    }


def create_eventsub_notification(
    subscription_type: str,
    version: str,
    channel: str,
) -> dict:
    """Build an editable EventSub envelope with useful event presets."""

    if subscription_type == "channel.chat.message":
        payload = create_chat_notification(channel, "Test_Viewer", "Hello Sally!")
        payload["subscription"]["version"] = version
        return payload

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    broadcaster = {
        "broadcaster_user_id": "1000",
        "broadcaster_user_login": channel,
        "broadcaster_user_name": channel,
    }
    viewer = {
        "user_id": "3000",
        "user_login": "test_viewer",
        "user_name": "Test_Viewer",
    }

    event: dict = {**broadcaster, "simulated": True}
    if subscription_type == "channel.follow":
        event = {**viewer, **broadcaster, "followed_at": now}
    elif subscription_type == "channel.subscribe":
        event = {
            **viewer,
            **broadcaster,
            "tier": "1000",
            "is_gift": False,
        }
    elif subscription_type == "channel.subscription.gift":
        event = {
            **viewer,
            **broadcaster,
            "total": 5,
            "tier": "1000",
            "cumulative_total": 12,
            "is_anonymous": False,
        }
    elif subscription_type == "channel.cheer":
        event = {
            "is_anonymous": False,
            **viewer,
            **broadcaster,
            "message": "Cheer100 Go Sally!",
            "bits": 100,
        }
    elif subscription_type == "channel.raid":
        event = {
            "from_broadcaster_user_id": "4000",
            "from_broadcaster_user_login": "raider",
            "from_broadcaster_user_name": "Raider",
            "to_broadcaster_user_id": "1000",
            "to_broadcaster_user_login": channel,
            "to_broadcaster_user_name": channel,
            "viewers": 42,
        }
    elif subscription_type == "channel.channel_points_custom_reward_redemption.add":
        event = {
            "id": str(uuid4()),
            **viewer,
            **broadcaster,
            "user_input": "Make Sally laugh",
            "status": "unfulfilled",
            "reward": {
                "id": "reward-1",
                "title": "Sally Interaction",
                "cost": 1000,
                "prompt": "Tell Sally what to do",
            },
            "redeemed_at": now,
        }
    elif subscription_type == "stream.online":
        event = {
            "id": str(uuid4()),
            **broadcaster,
            "type": "live",
            "started_at": now,
        }
    elif subscription_type == "stream.offline":
        event = broadcaster

    return {
        "subscription": {
            "id": str(uuid4()),
            "status": "enabled",
            "type": subscription_type,
            "version": version,
            "condition": {"broadcaster_user_id": "1000"},
            "transport": {
                "method": "webhook",
                "callback": "http://127.0.0.1/eventsub",
            },
            "created_at": now,
            "cost": 0,
        },
        "event": event,
    }


def send_signed_eventsub_request(
    url: str,
    secret: str,
    payload: dict,
    message_type: str = "notification",
    subscription_type: str = "channel.chat.message",
    message_id: str | None = None,
    timestamp: str | None = None,
    version: str = "1",
) -> int:
    """Send a signed Twitch-style request to a local EventSub listener."""

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    message_id = message_id or str(uuid4())
    timestamp = timestamp or datetime.now(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )
    signed_message = message_id.encode() + timestamp.encode() + body
    digest = hmac.new(
        secret.encode("ascii"),
        signed_message,
        hashlib.sha256,
    ).hexdigest()

    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Twitch-Eventsub-Message-Id": message_id,
            "Twitch-Eventsub-Message-Type": message_type,
            "Twitch-Eventsub-Message-Signature": f"sha256={digest}",
            "Twitch-Eventsub-Message-Timestamp": timestamp,
            "Twitch-Eventsub-Subscription-Type": subscription_type,
            "Twitch-Eventsub-Subscription-Version": version,
        },
    )
    with urlopen(request, timeout=3) as response:
        return response.status
