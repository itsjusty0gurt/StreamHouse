from __future__ import annotations

from enum import StrEnum
from secrets import token_hex
from urllib.error import HTTPError, URLError

from products.hub.config.twitch import TWITCH_REDEMPTION_SCOPES
from products.hub.core.events import Events
from shared.streamhouse_runtime.logger import Logger
from products.hub.twitch.eventsub import EventSubWebhookProcessor, LocalEventSubListener
from products.hub.twitch.auth import TwitchAuthService
from products.hub.twitch.live import TwitchEventSubSocket, TwitchHelixClient
from products.hub.twitch.models import (
    TwitchChatNotice,
    TwitchCustomReward,
    TwitchEvent,
    TwitchEventDiagnostic,
    TwitchMessage,
)
from datetime import datetime, timezone
from products.hub.twitch.simulator import (
    create_chat_notification,
    send_signed_eventsub_request,
)


class TwitchConnectionState(StrEnum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    ERROR = "Error"


class TwitchService:
    """UI-independent Twitch service with a local EventSub test transport."""

    def __init__(
        self,
        auth: TwitchAuthService | None = None,
        bot_auth: TwitchAuthService | None = None,
        helix: TwitchHelixClient | None = None,
    ) -> None:
        self.auth = auth
        self.bot_auth = bot_auth
        self.helix = helix or TwitchHelixClient()
        self.state = TwitchConnectionState.DISCONNECTED
        self.channel = ""
        self.eventsub_secret = token_hex(32)
        self.webhook_processor = EventSubWebhookProcessor(
            secret=self.eventsub_secret,
            on_chat_message=self._receive_chat_message,
            on_revocation=self._receive_revocation,
            on_diagnostic=self._receive_diagnostic,
            on_notification=self._receive_notification,
            on_bus_event=self._publish_bus_event,
        )
        self.local_listener = LocalEventSubListener(self.webhook_processor)
        self.live_socket: TwitchEventSubSocket | None = None
        self.activity_socket: TwitchEventSubSocket | None = None
        # Pin the identities selected when the sockets open. Twitch auth
        # restore completes asynchronously, so looking the tokens up again in
        # a later welcome callback can otherwise mix broadcaster and bot
        # subscriptions on the same WebSocket session.
        self._live_chat_token = None
        self._activity_token = None
        self.broadcaster_user_id = ""
        self.broadcaster_display_name = ""
        self.badge_urls: dict[tuple[str, str], str] = {}

    @property
    def listener_url(self) -> str:
        return self.local_listener.url

    def connect(self, channel: str) -> bool:
        clean_channel = channel.strip().lstrip("#").lower()

        if not clean_channel:
            self._report_error("A Twitch channel is required.")
            return False

        if self.state is TwitchConnectionState.CONNECTED:
            if self.channel == clean_channel:
                Logger.debug(
                    f'Already connected to Twitch channel "{clean_channel}".',
                    source="TWITCH",
                )
                return True
            self.disconnect()

        self.channel = clean_channel
        self._set_state(TwitchConnectionState.CONNECTING)
        if self.auth is not None and self.auth.token is None:
            self.channel = ""
            self._report_error("Sign in with Twitch before connecting.")
            return False

        if self.auth is not None:
            return self._connect_live(clean_channel)

        Logger.info(
            f'Connecting to Twitch channel "{clean_channel}" (simulated).',
            source="TWITCH",
        )

        try:
            self.local_listener.start()
        except OSError as error:
            self.channel = ""
            self._report_error(
                f"Could not start the local EventSub listener: {error}"
            )
            return False

        self._set_state(TwitchConnectionState.CONNECTED)
        Logger.info(
            f'Connected to Twitch channel "{clean_channel}" (simulated).',
            source="TWITCH",
        )
        return True

    def _connect_live(self, channel: str) -> bool:
        token = self.auth.token if self.auth is not None else None
        if token is None:
            self._report_error("Sign in with Twitch before connecting.")
            return False
        try:
            broadcaster = self.helix.get_user(channel, token)
            self.broadcaster_user_id = str(broadcaster["id"])
            self.broadcaster_display_name = str(
                broadcaster.get("display_name", broadcaster.get("login", channel))
            ).strip()
            bot_token = (
                self.bot_auth.token
                if self.bot_auth is not None
                else None
            )
            if (
                bot_token is not None
                and bot_token.user_id
                and bot_token.user_id == token.user_id
            ):
                raise ValueError(
                    "Your channel and bot account are signed into the same "
                    "Twitch account. Sign the channel account in as the "
                    "streamer and keep the separate bot account in the bot slot."
                )
            self.local_listener.start()
        except (HTTPError, URLError, OSError, ValueError, KeyError) as error:
            self.channel = ""
            self._report_error(f"Could not prepare Twitch connection: {error}")
            return False

        try:
            self.badge_urls = self.helix.get_badge_urls(
                self.broadcaster_user_id,
                token,
            )
        except (HTTPError, URLError, OSError, ValueError) as error:
            self.badge_urls = {}
            Logger.warning(
                f"Twitch badges could not be loaded: {error}",
                source="TWITCH",
            )

        self.live_socket = TwitchEventSubSocket(
            on_welcome=self._receive_live_welcome,
            on_message=self._receive_chat_message,
            on_notification=self._receive_notification,
            on_diagnostic=self._receive_diagnostic,
            on_revocation=self._receive_revocation,
            on_error=self._receive_live_error,
            on_bus_event=self._publish_bus_event,
        )
        self._live_chat_token = self._chat_token()
        self._activity_token = token
        self.live_socket.open()
        chat_token = self._live_chat_token
        if (
            chat_token is not None
            and token.user_id
            and chat_token.user_id
            and token.user_id != chat_token.user_id
        ):
            # Twitch binds WebSocket subscriptions to the authorizing user.
            # A separate bot therefore needs its own chat socket, while channel
            # activity remains on a broadcaster-authorized socket.
            self.activity_socket = TwitchEventSubSocket(
                on_welcome=self._receive_activity_welcome,
                on_message=self._receive_chat_message,
                on_notification=self._receive_notification,
                on_diagnostic=self._receive_diagnostic,
                on_revocation=self._receive_revocation,
                on_error=self._receive_activity_error,
                on_bus_event=self._publish_bus_event,
            )
            self.activity_socket.open()
        Logger.info(
            f'Opening live EventSub connection for "{channel}".',
            source="TWITCH",
        )
        return True

    def _receive_live_welcome(self, session_id: str) -> None:
        broadcaster_token = self._activity_token
        chat_token = self._live_chat_token
        if chat_token is None or not chat_token.user_id:
            self._report_error("The Twitch sign-in is missing its user identity.")
            return
        try:
            self.helix.create_chat_subscriptions(
                session_id,
                self.broadcaster_user_id,
                chat_token.user_id,
                chat_token,
            )
            if broadcaster_token is not None and self.activity_socket is None:
                warnings = self.helix.create_activity_subscriptions(
                    session_id,
                    self.broadcaster_user_id,
                    broadcaster_token.user_id,
                    broadcaster_token,
                )
                self._log_activity_warnings(warnings)
        except (HTTPError, URLError, OSError, ValueError) as error:
            self._report_error(f"Could not subscribe to Twitch chat: {error}")
            if self.live_socket is not None:
                self.live_socket.close()
            return
        self._set_state(TwitchConnectionState.CONNECTED)
        Logger.info(
            f'Connected to live Twitch chat for "{self.channel}".',
            source="TWITCH",
        )

    def _receive_activity_welcome(self, session_id: str) -> None:
        token = self._activity_token
        if token is None or not token.user_id:
            Logger.warning(
                "Channel activity EventSub is missing broadcaster identity.",
                source="TWITCH",
            )
            return
        try:
            warnings = self.helix.create_activity_subscriptions(
                session_id,
                self.broadcaster_user_id,
                token.user_id,
                token,
            )
        except (HTTPError, URLError, OSError, ValueError) as error:
            Logger.warning(
                f"Could not subscribe to optional Twitch activity: {error}",
                source="TWITCH",
            )
            return
        self._log_activity_warnings(warnings)

    @staticmethod
    def _log_activity_warnings(warnings: object) -> None:
        if not isinstance(warnings, (tuple, list)):
            return
        for warning in warnings:
            Logger.warning(
                f"Optional Twitch activity subscription unavailable: {warning}",
                source="TWITCH",
            )

    @staticmethod
    def _receive_activity_error(message: str) -> None:
        Logger.warning(
            f"Twitch activity EventSub connection issue: {message}",
            source="TWITCH",
        )

    def _receive_live_error(self, message: str) -> None:
        self._report_error(message)

    def send_message(self, text: str, *, as_bot: bool = True) -> bool:
        clean_text = text.strip()
        if self.state is not TwitchConnectionState.CONNECTED:
            self._report_error(
                "Connect to Twitch before sending a message.",
                change_state=False,
            )
            return False
        token = (
            self._chat_token()
            if as_bot
            else self.auth.token if self.auth is not None else None
        )
        if token is None or not token.user_id:
            self._report_error("Sign in with Twitch before sending.", change_state=False)
            return False
        if not clean_text:
            self._report_error("A chat message is required.", change_state=False)
            return False
        try:
            self.helix.send_chat_message(
                self.broadcaster_user_id,
                token.user_id,
                clean_text,
                token,
            )
        except (HTTPError, URLError, OSError, ValueError) as error:
            self._report_error(f"Could not send Twitch message: {error}", change_state=False)
            return False
        return True

    def send_pinned_message(self, text: str) -> tuple[bool, bool]:
        """Send through the bot account, then pin as the broadcaster."""

        clean_text = text.strip()
        if self.state is not TwitchConnectionState.CONNECTED:
            return False, False
        chat_token = self._chat_token()
        moderator_token = self.auth.token if self.auth is not None else None
        if (
            not clean_text
            or chat_token is None
            or not chat_token.user_id
            or moderator_token is None
            or not moderator_token.user_id
            or not self.broadcaster_user_id
        ):
            return False, False
        try:
            message_id = self.helix.send_chat_message(
                self.broadcaster_user_id,
                chat_token.user_id,
                clean_text,
                chat_token,
            )
        except (HTTPError, URLError, OSError, ValueError) as error:
            self._report_error(
                f"Could not send pinned Twitch message: {error}",
                change_state=False,
            )
            return False, False
        if not message_id:
            Logger.warning(
                "Twitch sent the training notice without returning a message ID; "
                "it could not be pinned.",
                source="TWITCH",
            )
            return True, False
        try:
            self.helix.pin_chat_message(
                self.broadcaster_user_id,
                moderator_token.user_id,
                message_id,
                moderator_token,
            )
        except (HTTPError, URLError, OSError, ValueError) as error:
            Logger.warning(
                f"Training notice was sent but could not be pinned: {error}",
                source="TWITCH",
            )
            return True, False
        return True, True

    def _chat_token(self):
        if self.bot_auth is not None and self.bot_auth.token is not None:
            return self.bot_auth.token
        return self.auth.token if self.auth is not None else None

    def badge_url(self, set_id: str, badge_id: str) -> str:
        return self.badge_urls.get((set_id, badge_id), "")

    def _broadcaster_credentials(self, required_scope: str = ""):
        token = self.auth.token if self.auth is not None else None
        broadcaster_id = self.broadcaster_user_id or (
            token.user_id if token is not None else ""
        )
        if token is None or not broadcaster_id:
            raise ValueError("Sign in with your channel account first.")
        if required_scope and required_scope not in set(token.scopes):
            raise ValueError(
                "Reconnect Twitch to grant the required permission: "
                f"{required_scope}."
            )
        return broadcaster_id, token

    def get_custom_rewards(self) -> list[TwitchCustomReward]:
        broadcaster_id, token = self._broadcaster_credentials()
        all_rewards = self.helix.get_custom_rewards(broadcaster_id, token)
        manageable_ids = {
            str(item.get("id", ""))
            for item in self.helix.get_custom_rewards(
                broadcaster_id, token, only_manageable=True
            )
        }
        return [
            TwitchCustomReward.from_dict(
                item,
                manageable=str(item.get("id", "")) in manageable_ids,
            )
            for item in all_rewards
        ]

    def get_custom_rewards_for_discovery(self) -> list[TwitchCustomReward]:
        """Return every custom reward using either supported redemption scope."""

        broadcaster_id, token = self._broadcaster_credentials()
        if not set(token.scopes).intersection(TWITCH_REDEMPTION_SCOPES):
            raise ValueError(
                "Reconnect Twitch to grant channel:read:redemptions or "
                "channel:manage:redemptions."
            )
        return [
            TwitchCustomReward.from_dict(item)
            for item in self.helix.get_custom_rewards(
                broadcaster_id, token, only_manageable=False
            )
        ]

    def create_custom_reward(
        self, values: dict[str, object]
    ) -> TwitchCustomReward:
        broadcaster_id, token = self._broadcaster_credentials()
        result = self.helix.create_custom_reward(
            broadcaster_id, values, token
        )
        return TwitchCustomReward.from_dict(result, manageable=True)

    def update_custom_reward(
        self,
        reward_id: str,
        values: dict[str, object],
    ) -> TwitchCustomReward:
        broadcaster_id, token = self._broadcaster_credentials()
        result = self.helix.update_custom_reward(
            broadcaster_id, reward_id, values, token
        )
        return TwitchCustomReward.from_dict(result, manageable=True)

    def delete_custom_reward(self, reward_id: str) -> None:
        broadcaster_id, token = self._broadcaster_credentials()
        self.helix.delete_custom_reward(broadcaster_id, reward_id, token)

    def update_redemption_status(
        self, reward_id: str, redemption_id: str, status: str
    ) -> dict:
        broadcaster_id, token = self._broadcaster_credentials()
        return self.helix.update_redemption_status(
            broadcaster_id,
            reward_id,
            redemption_id,
            status,
            token,
        )

    def run_commercial(self, length: int) -> dict:
        broadcaster_id, token = self._broadcaster_credentials(
            "channel:edit:commercial"
        )
        return self.helix.start_commercial(
            broadcaster_id, min(max(int(length), 30), 180), token
        )

    def snooze_next_ad(self) -> dict:
        broadcaster_id, token = self._broadcaster_credentials(
            "channel:manage:ads"
        )
        return self.helix.snooze_ad(broadcaster_id, token)

    def update_stream_title(self, title: str) -> None:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Enter a stream title.")
        broadcaster_id, token = self._broadcaster_credentials()
        self.helix.update_channel_information(
            broadcaster_id,
            {"title": clean_title[:140]},
            token,
        )

    def update_stream_category(self, category: str) -> str:
        clean_category = category.strip()
        if not clean_category:
            raise ValueError("Enter a Twitch category name.")
        broadcaster_id, token = self._broadcaster_credentials()
        matches = self.helix.search_categories(clean_category, token)
        exact = next(
            (
                item
                for item in matches
                if str(item.get("name", "")).casefold()
                == clean_category.casefold()
            ),
            None,
        )
        selected = exact or (matches[0] if len(matches) == 1 else None)
        if selected is None:
            raise ValueError(
                f'Twitch category "{clean_category}" was not an exact match.'
            )
        game_id = str(selected.get("id", "")).strip()
        category_name = str(selected.get("name", clean_category)).strip()
        if not game_id:
            raise ValueError("Twitch returned an invalid category.")
        self.helix.update_channel_information(
            broadcaster_id,
            {"game_id": game_id},
            token,
        )
        return category_name

    def resolve_user_id(self, reference: str) -> str:
        clean = reference.strip().lstrip("@")
        if not clean or clean == "--":
            raise ValueError("A Twitch user is required.")
        if clean.isdigit():
            return clean
        _broadcaster_id, token = self._broadcaster_credentials()
        user = self.helix.get_user(clean, token)
        user_id = str(user.get("id", ""))
        if not user_id:
            raise ValueError(f'Twitch user "{clean}" was not found.')
        return user_id

    def resolve_user(self, reference: str) -> dict:
        clean = reference.strip().lstrip("@")
        if not clean or clean == "--":
            raise ValueError("A Twitch user is required.")
        _broadcaster_id, token = self._broadcaster_credentials()
        return (
            self.helix.get_user_by_id(clean, token)
            if clean.isdigit()
            else self.helix.get_user(clean, token)
        )

    def get_stream_information(self) -> dict | None:
        broadcaster_id, token = self._broadcaster_credentials()
        return self.helix.get_stream_information(broadcaster_id, token)

    def get_channel_information(self) -> dict | None:
        broadcaster_id, token = self._broadcaster_credentials()
        return self.helix.get_channel_information(broadcaster_id, token)

    def get_follow_relationship(self, user_id: str) -> dict | None:
        broadcaster_id, token = self._broadcaster_credentials()
        if "moderator:read:followers" not in set(token.scopes):
            raise PermissionError(
                "Follow information requires the moderator:read:followers permission."
            )
        return self.helix.get_follow_relationship(
            broadcaster_id, user_id, token
        )

    def channel_display_name(self) -> str:
        return self.broadcaster_display_name or self.channel or "the channel"

    def moderate_user(
        self,
        action: str,
        user_id: str,
        *,
        message_id: str = "",
        duration: int | None = None,
        reason: str = "",
    ) -> bool:
        """Apply a moderation action using the signed-in Twitch identity."""
        token = self.auth.token if self.auth is not None else None
        if token is None or not token.user_id or not self.broadcaster_user_id:
            self._report_error(
                "Connect your Twitch account before moderating chat.",
                change_state=False,
            )
            return False
        try:
            if action in {"timeout", "ban"}:
                self.helix.ban_user(
                    self.broadcaster_user_id,
                    token.user_id,
                    user_id,
                    token,
                    duration=duration if action == "timeout" else None,
                    reason=reason,
                )
            elif action == "unban":
                self.helix.unban_user(
                    self.broadcaster_user_id,
                    token.user_id,
                    user_id,
                    token,
                )
            elif action == "delete_message" and message_id:
                self.helix.delete_chat_message(
                    self.broadcaster_user_id,
                    token.user_id,
                    message_id,
                    token,
                )
            else:
                raise ValueError("Unsupported Twitch moderation action.")
        except (HTTPError, URLError, OSError, ValueError) as error:
            self._report_error(
                f"Twitch moderation failed: {error}",
                change_state=False,
            )
            return False
        Logger.info(
            f'Twitch moderation action "{action}" completed for user {user_id}.',
            source="TWITCH",
        )
        return True

    def disconnect(self) -> bool:
        if self.state is TwitchConnectionState.DISCONNECTED:
            return False

        previous_channel = self.channel
        if self.live_socket is not None:
            self.live_socket.close()
            self.live_socket.deleteLater()
            self.live_socket = None
        if self.activity_socket is not None:
            self.activity_socket.close()
            self.activity_socket.deleteLater()
            self.activity_socket = None
        self._live_chat_token = None
        self._activity_token = None
        self.local_listener.stop()
        self.channel = ""
        self.broadcaster_user_id = ""
        self.broadcaster_display_name = ""
        self.badge_urls.clear()
        self._set_state(TwitchConnectionState.DISCONNECTED)
        Logger.info(
            f'Disconnected from Twitch channel "{previous_channel}".',
            source="TWITCH",
        )
        return True

    def simulate_message(self, username: str, text: str) -> bool:
        if self.state is not TwitchConnectionState.CONNECTED:
            self._report_error(
                "Connect to a Twitch channel before simulating chat.",
                change_state=False,
            )
            return False

        clean_username = username.strip()
        clean_text = text.strip()

        if not clean_username or not clean_text:
            self._report_error(
                "A username and chat message are required.",
                change_state=False,
            )
            return False

        payload = create_chat_notification(
            channel=self.channel,
            username=clean_username,
            text=clean_text,
        )
        try:
            status = send_signed_eventsub_request(
                url=self.listener_url,
                secret=self.eventsub_secret,
                payload=payload,
            )
        except (OSError, URLError) as error:
            self._report_error(
                f"Local EventSub simulation failed: {error}",
                change_state=False,
            )
            return False

        return status in (200, 202, 204)

    def simulate_event(
        self,
        subscription_type: str,
        version: str,
        payload: dict,
    ) -> bool:
        if self.state is not TwitchConnectionState.CONNECTED:
            self._report_error(
                "Connect to a Twitch channel before simulating events.",
                change_state=False,
            )
            return False

        if not subscription_type.strip() or not version.strip():
            self._report_error(
                "An EventSub type and version are required.",
                change_state=False,
            )
            return False

        try:
            status = send_signed_eventsub_request(
                url=self.listener_url,
                secret=self.eventsub_secret,
                payload=payload,
                subscription_type=subscription_type.strip(),
                version=version.strip(),
            )
        except (OSError, URLError) as error:
            self._report_error(
                f"Local EventSub simulation failed: {error}",
                change_state=False,
            )
            return False

        return status in (200, 202, 204)

    def _receive_chat_message(self, chat_message: TwitchMessage) -> None:
        # Chat content is intentionally not written to application logs. The
        # live UI and opted-in daily memory are the only content consumers.
        Logger.debug("Twitch chat message received.", source="TWITCH")
        Events.emit(
            "twitch_message_received",
            chat_message=chat_message,
        )

    def _receive_revocation(self, status: str) -> None:
        self._report_error(
            f"EventSub subscription revoked: {status}.",
        )

    @staticmethod
    def _receive_diagnostic(diagnostic: TwitchEventDiagnostic) -> None:
        Events.emit("twitch_event_received", diagnostic=diagnostic)

    @staticmethod
    def _receive_notification(
        subscription_type: str,
        payload: dict,
    ) -> None:
        Events.emit(
            "twitch_notification_received",
            subscription_type=subscription_type,
            payload=payload,
        )
        event = payload.get("event")
        if not isinstance(event, dict) or subscription_type == "channel.chat.message":
            return
        notice = TwitchService._make_chat_notice(subscription_type, event)
        if notice is not None:
            Events.emit("twitch_notice_received", notice=notice)

    @staticmethod
    def _make_chat_notice(
        subscription_type: str,
        event: dict,
    ) -> TwitchChatNotice | None:
        now = datetime.now(timezone.utc)
        if subscription_type == "channel.chat.clear":
            return TwitchChatNotice("clear", "Chat was cleared by a moderator.", now)
        if subscription_type == "channel.chat.clear_user_messages":
            login = str(event.get("target_user_login", "a user"))
            return TwitchChatNotice(
                "clear_user",
                f"Messages from {login} were cleared.",
                now,
                target_user_login=login,
            )
        if subscription_type == "channel.chat.message_delete":
            login = str(event.get("target_user_login", "a user"))
            return TwitchChatNotice(
                "delete",
                f"A message from {login} was deleted.",
                now,
                target_message_id=str(event.get("message_id", "")),
                target_user_login=login,
            )
        if subscription_type == "channel.chat.notification":
            notice_type = str(event.get("notice_type", "notification"))
            system_message = str(event.get("system_message", "")).strip()
            chatter = str(event.get("chatter_user_name", "Someone"))
            text = system_message or f"{chatter}: {notice_type.replace('_', ' ')}"
            return TwitchChatNotice(notice_type, text, now)
        return None

    @staticmethod
    def _publish_bus_event(twitch_event: TwitchEvent) -> None:
        Events.emit("twitch_event", twitch_event=twitch_event)
        Events.emit(
            f"twitch_event.{twitch_event.subscription_type}",
            twitch_event=twitch_event,
        )

    def _set_state(self, state: TwitchConnectionState) -> None:
        self.state = state
        Events.emit(
            "twitch_status_changed",
            state=state,
            channel=self.channel,
        )

    def _report_error(self, message: str, change_state: bool = True) -> None:
        if change_state:
            self.state = TwitchConnectionState.ERROR
        Logger.error(message, source="TWITCH")
        Events.emit("twitch_error", message=message)
        if change_state:
            Events.emit(
                "twitch_status_changed",
                state=self.state,
                channel=self.channel,
            )
