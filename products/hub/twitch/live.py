from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from products.hub.config.twitch import TWITCH_CLIENT_ID, TWITCH_REDEMPTION_SCOPES
from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.models import (
    TwitchEvent,
    TwitchEventDiagnostic,
    TwitchEventTransport,
    TwitchMessage,
)
from products.hub.twitch.parser import TwitchMessageParser, TwitchPayloadError


class TwitchHelixClient:
    """Small authenticated client for the Helix calls used by live chat."""

    USERS_URL = "https://api.twitch.tv/helix/users"
    SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
    SEND_URL = "https://api.twitch.tv/helix/chat/messages"
    PINS_URL = "https://api.twitch.tv/helix/chat/pins"
    GLOBAL_BADGES_URL = "https://api.twitch.tv/helix/chat/badges/global"
    CHANNEL_BADGES_URL = "https://api.twitch.tv/helix/chat/badges"
    STREAMS_URL = "https://api.twitch.tv/helix/streams"
    CHANNELS_URL = "https://api.twitch.tv/helix/channels"
    SEARCH_CATEGORIES_URL = "https://api.twitch.tv/helix/search/categories"
    FOLLOWERS_URL = "https://api.twitch.tv/helix/channels/followers"
    CHANNEL_SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/subscriptions"
    CHATTERS_URL = "https://api.twitch.tv/helix/chat/chatters"
    MODERATORS_URL = "https://api.twitch.tv/helix/moderation/moderators"
    VIPS_URL = "https://api.twitch.tv/helix/channels/vips"
    AD_SCHEDULE_URL = "https://api.twitch.tv/helix/channels/ads"
    SNOOZE_AD_URL = "https://api.twitch.tv/helix/channels/ads/schedule/snooze"
    COMMERCIAL_URL = "https://api.twitch.tv/helix/channels/commercial"
    BANS_URL = "https://api.twitch.tv/helix/moderation/bans"
    DELETE_CHAT_URL = "https://api.twitch.tv/helix/moderation/chat"
    CUSTOM_REWARDS_URL = (
        "https://api.twitch.tv/helix/channel_points/custom_rewards"
    )
    REDEMPTIONS_URL = (
        "https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions"
    )
    CHAT_SUBSCRIPTIONS = (
        "channel.chat.message",
        "channel.chat.notification",
        "channel.chat.clear",
        "channel.chat.clear_user_messages",
        "channel.chat.message_delete",
    )

    @staticmethod
    def _headers(token: TwitchToken) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token.access_token}",
            "Client-Id": TWITCH_CLIENT_ID,
        }

    @staticmethod
    def _read_json(
        request: Request,
        timeout: float = 15,
        attempts: int = 3,
    ) -> dict[str, Any]:
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=timeout) as response:
                    value = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if (
                    error.code not in {429, 500, 502, 503, 504}
                    or attempt + 1 >= attempts
                ):
                    raise
                retry_after = float(error.headers.get("Retry-After", "1"))
                time.sleep(min(max(retry_after, 0.1), 5.0))
            except URLError:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(0.25 * (2 ** attempt))
        if not isinstance(value, dict):
            raise ValueError("Twitch returned an invalid API response.")
        return value

    def _get_paginated(
        self,
        url: str,
        token: TwitchToken,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        next_url = url
        for _ in range(max_pages):
            payload = self._read_json(
                Request(next_url, headers=self._headers(token))
            )
            records.extend(
                item
                for item in payload.get("data", [])
                if isinstance(item, dict)
            )
            pagination = payload.get("pagination", {})
            cursor = (
                str(pagination.get("cursor", ""))
                if isinstance(pagination, dict)
                else ""
            )
            if not cursor:
                break
            separator = "&" if "?" in url else "?"
            next_url = f"{url}{separator}{urlencode({'after': cursor})}"
        return records

    def get_user(self, login: str, token: TwitchToken) -> dict[str, Any]:
        url = f"{self.USERS_URL}?{urlencode({'login': login})}"
        return self._get_single_user(url, login, token)

    def get_user_by_id(self, user_id: str, token: TwitchToken) -> dict[str, Any]:
        url = f"{self.USERS_URL}?{urlencode({'id': user_id})}"
        return self._get_single_user(url, user_id, token)

    def _get_single_user(
        self,
        url: str,
        reference: str,
        token: TwitchToken,
    ) -> dict[str, Any]:
        payload = self._read_json(Request(url, headers=self._headers(token)))
        users = payload.get("data")
        if not isinstance(users, list) or not users:
            raise ValueError(f'Twitch user "{reference}" was not found.')
        user = users[0]
        if not isinstance(user, dict) or not user.get("id"):
            raise ValueError("Twitch returned invalid user information.")
        return user

    def get_stream_information(
        self, broadcaster_id: str, token: TwitchToken
    ) -> dict[str, Any] | None:
        payload = self._read_json(
            Request(
                f"{self.STREAMS_URL}?{urlencode({'user_id': broadcaster_id})}",
                headers=self._headers(token),
            )
        )
        values = payload.get("data")
        if not isinstance(values, list) or not values:
            return None
        return values[0] if isinstance(values[0], dict) else None

    def get_channel_information(
        self, broadcaster_id: str, token: TwitchToken
    ) -> dict[str, Any] | None:
        payload = self._read_json(
            Request(
                f"{self.CHANNELS_URL}?{urlencode({'broadcaster_id': broadcaster_id})}",
                headers=self._headers(token),
            )
        )
        values = payload.get("data")
        if not isinstance(values, list) or not values:
            return None
        return values[0] if isinstance(values[0], dict) else None

    def get_follow_relationship(
        self,
        broadcaster_id: str,
        user_id: str,
        token: TwitchToken,
    ) -> dict[str, Any] | None:
        payload = self._read_json(
            Request(
                f"{self.FOLLOWERS_URL}?"
                f"{urlencode({'broadcaster_id': broadcaster_id, 'user_id': user_id, 'first': 1})}",
                headers=self._headers(token),
            )
        )
        values = payload.get("data")
        if not isinstance(values, list) or not values:
            return None
        return values[0] if isinstance(values[0], dict) else None

    def create_chat_subscriptions(
        self,
        session_id: str,
        broadcaster_user_id: str,
        chatter_user_id: str,
        token: TwitchToken,
    ) -> None:
        for subscription_type in self.CHAT_SUBSCRIPTIONS:
            self._create_subscription(
                subscription_type,
                "1",
                {
                    "broadcaster_user_id": broadcaster_user_id,
                    "user_id": chatter_user_id,
                },
                session_id,
                token,
            )

    def create_activity_subscriptions(
        self,
        session_id: str,
        broadcaster_user_id: str,
        chatter_user_id: str,
        token: TwitchToken,
    ) -> tuple[str, ...]:

        scopes = set(token.scopes)
        activity_specs = []
        if "moderator:read:followers" in scopes:
            activity_specs.append((
                "channel.follow", "2",
                {"broadcaster_user_id": broadcaster_user_id, "moderator_user_id": chatter_user_id},
            ))
        if "channel:read:subscriptions" in scopes:
            for event_type in (
                "channel.subscribe",
                "channel.subscription.gift",
                "channel.subscription.message",
            ):
                activity_specs.append((event_type, "1", {"broadcaster_user_id": broadcaster_user_id}))
        if "bits:read" in scopes:
            activity_specs.append(("channel.cheer", "1", {"broadcaster_user_id": broadcaster_user_id}))
        if "channel:read:ads" in scopes:
            activity_specs.append((
                "channel.ad_break.begin",
                "1",
                {"broadcaster_user_id": broadcaster_user_id},
            ))
        if scopes.intersection(TWITCH_REDEMPTION_SCOPES):
            activity_specs.append((
                "channel.channel_points_custom_reward_redemption.add",
                "1",
                {"broadcaster_user_id": broadcaster_user_id},
            ))
        activity_specs.extend(
            (
                (
                    "stream.online",
                    "1",
                    {"broadcaster_user_id": broadcaster_user_id},
                ),
                (
                    "stream.offline",
                    "1",
                    {"broadcaster_user_id": broadcaster_user_id},
                ),
            )
        )
        activity_specs.append(
            (
                "channel.raid",
                "1",
                {"to_broadcaster_user_id": broadcaster_user_id},
            )
        )
        warnings = []
        for event_type, version, condition in activity_specs:
            try:
                self._create_subscription(
                    event_type, version, condition, session_id, token
                )
            except (OSError, ValueError, URLError) as error:
                warnings.append(f"{event_type}: {error}")
        return tuple(warnings)

    def _create_subscription(
        self,
        event_type: str,
        version: str,
        condition: dict[str, str],
        session_id: str,
        token: TwitchToken,
    ) -> None:
        body = json.dumps({
            "type": event_type,
            "version": version,
            "condition": condition,
            "transport": {"method": "websocket", "session_id": session_id},
        }).encode("utf-8")
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        try:
            self._read_json(
                Request(
                    self.SUBSCRIPTIONS_URL,
                    data=body,
                    headers=headers,
                    method="POST",
                )
            )
        except HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")
            except OSError:
                detail = ""
            raise ValueError(
                f'Twitch rejected EventSub subscription "{event_type}" '
                f"with HTTP {error.code}"
                + (f": {detail}" if detail else ".")
            ) from error

    def send_chat_message(
        self,
        broadcaster_user_id: str,
        sender_user_id: str,
        text: str,
        token: TwitchToken,
    ) -> str:
        body = json.dumps(
            {
                "broadcaster_id": broadcaster_user_id,
                "sender_id": sender_user_id,
                "message": text,
            }
        ).encode("utf-8")
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        payload = self._read_json(
            Request(self.SEND_URL, data=body, headers=headers, method="POST")
        )
        results = payload.get("data")
        if not isinstance(results, list) or not results:
            raise ValueError("Twitch did not return a message result.")
        result = results[0]
        if not isinstance(result, dict):
            raise ValueError("Twitch returned an invalid message result.")
        if not result.get("is_sent"):
            reason = result.get("drop_reason")
            detail = (
                str(reason.get("message", "Twitch rejected the message."))
                if isinstance(reason, dict)
                else "Twitch rejected the message."
            )
            raise ValueError(detail)
        return str(result.get("message_id", ""))

    def pin_chat_message(
        self,
        broadcaster_id: str,
        moderator_id: str,
        message_id: str,
        token: TwitchToken,
    ) -> None:
        """Pin an existing message until the current stream ends."""

        parameters = {
            "broadcaster_id": broadcaster_id,
            "moderator_id": moderator_id,
            "message_id": message_id,
        }
        url = f"{self.PINS_URL}?{urlencode(parameters)}"
        with urlopen(
            Request(url, headers=self._headers(token), method="PUT"),
            timeout=15,
        ):
            pass

    def ban_user(
        self,
        broadcaster_id: str,
        moderator_id: str,
        user_id: str,
        token: TwitchToken,
        *,
        duration: int | None = None,
        reason: str = "",
    ) -> None:
        data: dict[str, Any] = {"user_id": user_id}
        if duration is not None:
            data["duration"] = min(max(int(duration), 1), 1_209_600)
        if reason.strip():
            data["reason"] = reason.strip()[:500]
        body = json.dumps({"data": data}).encode("utf-8")
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        url = f"{self.BANS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'moderator_id': moderator_id})}"
        self._read_json(Request(url, data=body, headers=headers, method="POST"))

    def unban_user(
        self,
        broadcaster_id: str,
        moderator_id: str,
        user_id: str,
        token: TwitchToken,
    ) -> None:
        url = f"{self.BANS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'moderator_id': moderator_id, 'user_id': user_id})}"
        with urlopen(
            Request(url, headers=self._headers(token), method="DELETE"),
            timeout=15,
        ):
            pass

    def delete_chat_message(
        self,
        broadcaster_id: str,
        moderator_id: str,
        message_id: str,
        token: TwitchToken,
    ) -> None:
        url = f"{self.DELETE_CHAT_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'moderator_id': moderator_id, 'message_id': message_id})}"
        with urlopen(
            Request(url, headers=self._headers(token), method="DELETE"),
            timeout=15,
        ):
            pass

    def get_badge_urls(
        self,
        broadcaster_user_id: str,
        token: TwitchToken,
    ) -> dict[tuple[str, str], str]:
        requests = (
            Request(self.GLOBAL_BADGES_URL, headers=self._headers(token)),
            Request(
                f"{self.CHANNEL_BADGES_URL}?"
                f"{urlencode({'broadcaster_id': broadcaster_user_id})}",
                headers=self._headers(token),
            ),
        )
        badges: dict[tuple[str, str], str] = {}
        for request in requests:
            payload = self._read_json(request)
            for badge_set in payload.get("data", []):
                if not isinstance(badge_set, dict):
                    continue
                set_id = str(badge_set.get("set_id", ""))
                for version in badge_set.get("versions", []):
                    if isinstance(version, dict):
                        badges[(set_id, str(version.get("id", "")))] = str(
                            version.get("image_url_1x", "")
                        )
        return badges

    def search_categories(
        self,
        query: str,
        token: TwitchToken,
    ) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("Enter a Twitch category name.")
        payload = self._read_json(
            Request(
                f"{self.SEARCH_CATEGORIES_URL}?"
                f"{urlencode({'query': clean_query, 'first': 20})}",
                headers=self._headers(token),
            )
        )
        return [
            item for item in payload.get("data", []) if isinstance(item, dict)
        ]

    def update_channel_information(
        self,
        broadcaster_id: str,
        values: dict[str, str],
        token: TwitchToken,
    ) -> None:
        allowed = {
            key: str(value).strip()
            for key, value in values.items()
            if key in {"title", "game_id"} and str(value).strip()
        }
        if not allowed:
            raise ValueError("Choose a stream title or category to update.")
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.CHANNELS_URL}?{urlencode({'broadcaster_id': broadcaster_id})}",
            data=json.dumps(allowed).encode("utf-8"),
            headers=headers,
            method="PATCH",
        )
        with urlopen(request, timeout=15):
            pass

    def get_channel_snapshot(self, broadcaster_id: str, token: TwitchToken) -> dict:
        headers = self._headers(token)
        stream_payload = self._read_json(Request(
            f"{self.STREAMS_URL}?{urlencode({'user_id': broadcaster_id})}", headers=headers
        ))
        channel_payload = self._read_json(Request(
            f"{self.CHANNELS_URL}?{urlencode({'broadcaster_id': broadcaster_id})}",
            headers=headers,
        ))
        result = {
            "stream": (stream_payload.get("data") or [None])[0],
            "channel": (channel_payload.get("data") or [None])[0],
            "followers": None,
            "subscribers": None,
            "ad_schedule": None,
            "warnings": [],
        }
        scopes = set(token.scopes)
        if "moderator:read:followers" in scopes:
            try:
                follower_payload = self._read_json(Request(
                    f"{self.FOLLOWERS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'first': 1})}", headers=headers
                ))
                result["followers"] = int(follower_payload.get("total", 0))
            except (HTTPError, URLError, OSError, ValueError) as error:
                result["warnings"].append(f"followers: {error}")
        if "channel:read:subscriptions" in scopes:
            try:
                subscriptions = self._read_json(Request(
                    f"{self.CHANNEL_SUBSCRIPTIONS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'first': 1})}", headers=headers
                ))
                result["subscribers"] = int(subscriptions.get("total", 0))
            except (HTTPError, URLError, OSError, ValueError) as error:
                result["warnings"].append(f"subscribers: {error}")
        if "channel:read:ads" in scopes:
            try:
                ad_payload = self._read_json(
                    Request(
                        f"{self.AD_SCHEDULE_URL}?"
                        f"{urlencode({'broadcaster_id': broadcaster_id})}",
                        headers=headers,
                    )
                )
                result["ad_schedule"] = (ad_payload.get("data") or [None])[0]
            except (HTTPError, URLError, OSError, ValueError) as error:
                result["warnings"].append(f"ad schedule: {error}")
        return result

    def get_chatters(self, broadcaster_id: str, moderator_id: str, token: TwitchToken) -> list[dict]:
        url = f"{self.CHATTERS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'moderator_id': moderator_id, 'first': 100})}"
        return self._get_paginated(url, token)

    def get_followers(
        self,
        broadcaster_id: str,
        token: TwitchToken,
    ) -> list[dict[str, Any]]:
        url = (
            f"{self.FOLLOWERS_URL}?"
            f"{urlencode({'broadcaster_id': broadcaster_id, 'first': 100})}"
        )
        return self._get_paginated(url, token)

    def get_chat_roles(
        self,
        broadcaster_id: str,
        token: TwitchToken,
    ) -> tuple[set[str], set[str], set[str]]:
        moderators = self._get_paginated(
            f"{self.MODERATORS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'first': 100})}",
            token,
        )
        vips = self._get_paginated(
            f"{self.VIPS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'first': 100})}",
            token,
        )
        moderator_ids = {
            str(item.get("user_id", ""))
            for item in moderators
        }
        vip_ids = {
            str(item.get("user_id", ""))
            for item in vips
        }
        subscribers = self._get_paginated(
            f"{self.CHANNEL_SUBSCRIPTIONS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'first': 100})}",
            token,
        )
        subscriber_ids = {
            str(item.get("user_id", ""))
            for item in subscribers
        }
        return moderator_ids, vip_ids, subscriber_ids

    def start_commercial(self, broadcaster_id: str, length: int, token: TwitchToken) -> dict:
        body = json.dumps({"broadcaster_id": broadcaster_id, "length": length}).encode()
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        try:
            payload = self._read_json(
                Request(
                    self.COMMERCIAL_URL,
                    data=body,
                    headers=headers,
                    method="POST",
                )
            )
        except HTTPError as error:
            self._raise_ads_api_error("start the commercial", error)
        return self._ads_result(payload, "commercial")

    def snooze_ad(self, broadcaster_id: str, token: TwitchToken) -> dict:
        url = f"{self.SNOOZE_AD_URL}?{urlencode({'broadcaster_id': broadcaster_id})}"
        try:
            payload = self._read_json(
                Request(
                    url,
                    data=b"",
                    headers=self._headers(token),
                    method="POST",
                )
            )
        except HTTPError as error:
            self._raise_ads_api_error("snooze the next ad", error)
        return self._ads_result(payload, "ad action")

    @staticmethod
    def _ads_result(payload: dict[str, Any], action: str) -> dict[str, Any]:
        values = payload.get("data")
        if (
            not isinstance(values, list)
            or not values
            or not isinstance(values[0], dict)
        ):
            raise ValueError(f"Twitch returned no result for the {action}.")
        return values[0]

    @staticmethod
    def _raise_ads_api_error(action: str, error: HTTPError) -> None:
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", errors="replace"))
            if isinstance(payload, dict):
                detail = str(payload.get("message") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
        raise ValueError(
            f"Twitch could not {action} (HTTP {error.code})"
            + (f": {detail}" if detail else ".")
        ) from error

    def get_custom_rewards(
        self,
        broadcaster_id: str,
        token: TwitchToken,
        *,
        only_manageable: bool = False,
    ) -> list[dict[str, Any]]:
        parameters = {
            "broadcaster_id": broadcaster_id,
            "only_manageable_rewards": str(only_manageable).lower(),
        }
        payload = self._read_json(
            Request(
                f"{self.CUSTOM_REWARDS_URL}?{urlencode(parameters)}",
                headers=self._headers(token),
            )
        )
        return [
            item for item in payload.get("data", []) if isinstance(item, dict)
        ]

    def create_custom_reward(
        self,
        broadcaster_id: str,
        values: dict[str, Any],
        token: TwitchToken,
    ) -> dict[str, Any]:
        return self._write_custom_reward(
            "POST", broadcaster_id, "", values, token
        )

    def update_custom_reward(
        self,
        broadcaster_id: str,
        reward_id: str,
        values: dict[str, Any],
        token: TwitchToken,
    ) -> dict[str, Any]:
        return self._write_custom_reward(
            "PATCH", broadcaster_id, reward_id, values, token
        )

    def _write_custom_reward(
        self,
        method: str,
        broadcaster_id: str,
        reward_id: str,
        values: dict[str, Any],
        token: TwitchToken,
    ) -> dict[str, Any]:
        parameters = {"broadcaster_id": broadcaster_id}
        if reward_id:
            parameters["id"] = reward_id
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        payload = self._read_json(
            Request(
                f"{self.CUSTOM_REWARDS_URL}?{urlencode(parameters)}",
                data=json.dumps(values).encode("utf-8"),
                headers=headers,
                method=method,
            )
        )
        rewards = payload.get("data", [])
        if not isinstance(rewards, list) or not rewards:
            raise ValueError("Twitch did not return the custom reward.")
        reward = rewards[0]
        if not isinstance(reward, dict):
            raise ValueError("Twitch returned an invalid custom reward.")
        return reward

    def delete_custom_reward(
        self,
        broadcaster_id: str,
        reward_id: str,
        token: TwitchToken,
    ) -> None:
        url = f"{self.CUSTOM_REWARDS_URL}?{urlencode({'broadcaster_id': broadcaster_id, 'id': reward_id})}"
        with urlopen(
            Request(url, headers=self._headers(token), method="DELETE"),
            timeout=15,
        ):
            pass

    def update_redemption_status(
        self,
        broadcaster_id: str,
        reward_id: str,
        redemption_id: str,
        status: str,
        token: TwitchToken,
    ) -> dict[str, Any]:
        parameters = {
            "broadcaster_id": broadcaster_id,
            "reward_id": reward_id,
            "id": redemption_id,
        }
        headers = self._headers(token)
        headers["Content-Type"] = "application/json"
        payload = self._read_json(
            Request(
                f"{self.REDEMPTIONS_URL}?{urlencode(parameters)}",
                data=json.dumps({"status": status}).encode("utf-8"),
                headers=headers,
                method="PATCH",
            )
        )
        results = payload.get("data", [])
        return results[0] if isinstance(results, list) and results else {}


class TwitchEventSubSocket(QObject):
    """Receive Twitch EventSub messages using Qt's WebSocket transport."""

    DEFAULT_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"

    def __init__(
        self,
        on_welcome: Callable[[str], None],
        on_message: Callable[[TwitchMessage], None],
        on_notification: Callable[[str, dict[str, Any]], None],
        on_diagnostic: Callable[[TwitchEventDiagnostic], None],
        on_revocation: Callable[[str], None],
        on_error: Callable[[str], None],
        on_bus_event: Callable[[TwitchEvent], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_welcome = on_welcome
        self.on_message = on_message
        self.on_notification = on_notification
        self.on_diagnostic = on_diagnostic
        self.on_revocation = on_revocation
        self.on_error = on_error
        self.on_bus_event = on_bus_event
        self.socket = QWebSocket(parent=self)
        self.socket.textMessageReceived.connect(self._receive_text)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.errorOccurred.connect(self._socket_error)
        self._intentional_close = True
        self._reconnect_transfer = False
        self._url = self.DEFAULT_URL
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()

    def open(self, url: str | None = None) -> None:
        self._intentional_close = False
        self._url = url or self.DEFAULT_URL
        self.socket.open(QUrl(self._url))

    def close(self) -> None:
        self._intentional_close = True
        self.socket.close()

    def _receive_text(self, text: str) -> None:
        try:
            message = json.loads(text)
            if not isinstance(message, dict):
                raise ValueError
            metadata = message.get("metadata", {})
            payload = message.get("payload", {})
            if not isinstance(metadata, dict) or not isinstance(payload, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            self.on_error("Twitch sent an invalid WebSocket message.")
            return

        message_id = str(metadata.get("message_id", ""))
        if message_id and self._is_duplicate(message_id):
            return
        message_type = str(metadata.get("message_type", ""))

        if message_type == "session_welcome":
            session = payload.get("session", {})
            session_id = str(session.get("id", "")) if isinstance(session, dict) else ""
            if not session_id:
                self.on_error("Twitch WebSocket welcome omitted its session ID.")
                return
            if self._reconnect_transfer:
                self._reconnect_transfer = False
            else:
                self.on_welcome(session_id)
            return

        if message_type == "session_reconnect":
            session = payload.get("session", {})
            reconnect_url = (
                str(session.get("reconnect_url", ""))
                if isinstance(session, dict)
                else ""
            )
            if reconnect_url:
                self._reconnect_transfer = True
                self._intentional_close = True
                self.socket.close()
                QTimer.singleShot(0, lambda: self.open(reconnect_url))
            return

        if message_type == "revocation":
            subscription = payload.get("subscription", {})
            status = str(subscription.get("status", "unknown"))
            self.on_revocation(status)
            self._diagnose(metadata, payload, "Processed", f"Revoked: {status}")
            return

        if message_type != "notification":
            return

        subscription_type = str(metadata.get("subscription_type", "unknown"))
        event = payload.get("event")
        if not isinstance(event, dict):
            self._diagnose(metadata, payload, "Error", "Missing event object")
            return
        try:
            received_at = datetime.fromisoformat(
                str(metadata.get("message_timestamp", "")).replace("Z", "+00:00")
            )
        except ValueError:
            received_at = datetime.now(timezone.utc)
        if subscription_type == "channel.chat.message":
            try:
                self.on_message(TwitchMessageParser.parse(event, received_at))
            except TwitchPayloadError:
                self._diagnose(metadata, payload, "Error", "Invalid chat payload")
                return
        self.on_notification(subscription_type, payload)
        if self.on_bus_event is not None:
            subscription = payload.get("subscription", {})
            self.on_bus_event(
                TwitchEvent(
                    subscription_type=subscription_type,
                    version=(
                        str(subscription.get("version", ""))
                        if isinstance(subscription, dict)
                        else ""
                    ),
                    received_at=received_at,
                    message_id=message_id,
                    broadcaster_user_id=str(event.get("broadcaster_user_id", "")),
                    broadcaster_user_login=str(event.get("broadcaster_user_login", "")),
                    broadcaster_user_name=str(event.get("broadcaster_user_name", "")),
                    transport=TwitchEventTransport.WEBSOCKET,
                    payload=payload,
                )
            )
        self._diagnose(metadata, payload, "Processed", f"Live {subscription_type}")

    def _diagnose(
        self,
        metadata: dict[str, Any],
        payload: dict[str, Any],
        result: str,
        summary: str,
    ) -> None:
        try:
            received_at = datetime.fromisoformat(
                str(metadata.get("message_timestamp", "")).replace("Z", "+00:00")
            )
        except ValueError:
            received_at = datetime.now(timezone.utc)
        self.on_diagnostic(
            TwitchEventDiagnostic(
                received_at=received_at,
                message_id=str(metadata.get("message_id", "")),
                message_type=str(metadata.get("message_type", "unknown")),
                subscription_type=str(metadata.get("subscription_type", "unknown")),
                result=result,
                summary=summary,
                status_code=0,
                headers={},
                payload=payload,
            )
        )

    def _disconnected(self) -> None:
        if not self._intentional_close:
            QTimer.singleShot(2000, self._reopen_if_needed)

    def _reopen_if_needed(self) -> None:
        if not self._intentional_close:
            self.open()

    def _socket_error(self, error: QAbstractSocket.SocketError) -> None:
        if not self._intentional_close:
            self.on_error(f"Twitch WebSocket error: {self.socket.errorString()}")

    def _is_duplicate(self, message_id: str) -> bool:
        if message_id in self._seen_ids:
            return True
        self._seen_ids.add(message_id)
        self._seen_order.append(message_id)
        if len(self._seen_order) > 1000:
            self._seen_ids.discard(self._seen_order.popleft())
        return False
