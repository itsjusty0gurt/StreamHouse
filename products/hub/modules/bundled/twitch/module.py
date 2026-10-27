from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer

from products.hub.automation.models import TriggerEvent
from products.hub.automation.task_catalog import TWITCH_TASK_METADATA
from products.hub.automation.variable_providers import twitch_context_provider
from products.hub.config.twitch import TWITCH_BOT_SCOPES
from products.hub.modules.contracts import Availability, ModuleContext, ModuleDescriptor
from products.hub.modules.contributions import TriggerProviderContribution
from products.hub.twitch.ads import AdsDomainEvent, AdsService
from products.hub.twitch.auth import TwitchAuthService, TwitchAuthState
from products.hub.twitch.automation_triggers import (
    OUTGOING_RAID_STARTED_EVENT_TYPE,
    TWITCH_TRIGGER_DISPLAY_NAMES,
    TwitchEventTriggerStore,
    TwitchSubscriptionEventCorrelator,
)
from products.hub.twitch.models import TwitchEvent, TwitchMessage
from products.hub.twitch.raid_contract import OutgoingRaidStartedEvent
from products.hub.twitch.service import TwitchConnectionState, TwitchService
from products.hub.twitch.tasks import TWITCH_TASK_LABELS
from products.hub.twitch.token_store import TwitchTokenStore
from shared.streamhouse_runtime.json_store import UnsupportedJsonSchemaError
from shared.streamhouse_runtime.logger import Logger


TWITCH_MODULE_ID = "streamhouse.twitch"
TWITCH_TRIGGER_PROVIDER_ID = "twitch.automation"


class TwitchModule:
    """First-party Twitch catalog and live runtime lifecycle owner."""

    descriptor = ModuleDescriptor(
        module_id=TWITCH_MODULE_ID,
        display_name="Twitch",
        version="0.3.0-alpha",
        category="Streaming Platform",
        enabled_by_default=True,
        required_core_capabilities=frozenset(
            {"automation", "tasks", "triggers", "variables"}
        ),
        capabilities=frozenset(
            {
                "twitch.automation",
                "twitch.commands",
                "twitch.variables",
            }
        ),
        ui_label="Twitch",
    )

    def __init__(self) -> None:
        self.auth = TwitchAuthService()
        self.bot_auth = TwitchAuthService(
            store=TwitchTokenStore.bot_account(),
            scopes=TWITCH_BOT_SCOPES,
            event_name="twitch_bot_auth_changed",
            account_label="Bot account",
        )
        self.service = TwitchService(auth=self.auth, bot_auth=self.bot_auth)
        self.ads_service = AdsService(self.service)
        self.trigger_store: TwitchEventTriggerStore | None = None
        self._context: ModuleContext | None = None
        self._enabled = False
        self._catalog_registered = False
        self._started = False
        self._maintenance_timer: QTimer | None = None
        self._correlation_timer: QTimer | None = None
        self._channel_snapshot_timer: QTimer | None = None
        self._stream_overview_timer: QTimer | None = None
        self._refresh_channel_snapshot: Callable[[], None] | None = None
        self._update_stream_overview: Callable[[], None] | None = None
        self._subscription_correlator = TwitchSubscriptionEventCorrelator()
        self._channel_snapshot: dict[str, object] = {}

    def availability(self) -> Availability:
        return Availability(
            self._enabled,
            "The Twitch module is disabled or unavailable." if not self._enabled else "",
        )

    def task_availability(self) -> tuple[bool, str]:
        availability = self.availability()
        return availability.available, availability.detail

    def bind_trigger_store(self, store: TwitchEventTriggerStore) -> None:
        if self._catalog_registered:
            raise RuntimeError("The Twitch trigger catalog is already registered.")
        self.trigger_store = store

    def bind_runtime_callbacks(
        self,
        *,
        refresh_channel_snapshot: Callable[[], None],
        update_stream_overview: Callable[[], None],
    ) -> None:
        """Bind transitional Twitch UI consumers before runtime startup."""

        if self._started:
            raise RuntimeError("Twitch runtime callbacks must be bound before startup.")
        self._refresh_channel_snapshot = refresh_channel_snapshot
        self._update_stream_overview = update_stream_overview

    def register_catalog(self, context: ModuleContext) -> None:
        if self._catalog_registered:
            raise ValueError("The Twitch static catalog is already registered.")
        for metadata in TWITCH_TASK_METADATA:
            context.tasks.register_metadata(metadata)
        context.tasks.register_provider_availability(
            TWITCH_TASK_LABELS,
            self.task_availability,
        )
        context.variables.register(
            twitch_context_provider(available=lambda: self._enabled)
        )

        self._context = context
        data_root = Path(context.data_root) if context.data_root is not None else None
        if data_root is None or context.routine_store is None:
            raise ValueError(
                "Twitch static catalog requires Core data-root and routine-store contracts."
            )
        if self.trigger_store is None:
            self.trigger_store = TwitchEventTriggerStore(
                data_root / "twitch" / "event_triggers.json",
                context.routine_store,
            )
        self._load_trigger_store()
        if context.trigger_providers is None:
            raise ValueError("Twitch static catalog requires trigger-provider support.")
        context.trigger_providers.register(
            TriggerProviderContribution(
                provider_id=TWITCH_TRIGGER_PROVIDER_ID,
                owner_module_id=TWITCH_MODULE_ID,
                display_name="Twitch",
                store=self.trigger_store,
                event_types=TWITCH_TRIGGER_DISPLAY_NAMES,
                availability=self.availability,
            )
        )
        self._catalog_registered = True

    def _load_trigger_store(self) -> None:
        assert self.trigger_store is not None
        try:
            self.trigger_store.load()
        except UnsupportedJsonSchemaError as error:
            try:
                removed = self.trigger_store.reset_obsolete_schema()
            except (OSError, TypeError, ValueError) as reset_error:
                self.trigger_store.triggers = []
                Logger.warning(
                    f"Could not reset obsolete Twitch automation triggers: {reset_error}",
                    source="TWITCH",
                )
            else:
                Logger.warning(
                    "Reset obsolete pre-Alpha Twitch automation triggers "
                    f"and removed {removed} routine link(s): {error}",
                    source="TWITCH",
                )
        except (OSError, TypeError, ValueError) as error:
            self.trigger_store.triggers = []
            Logger.warning(
                f"Could not load Twitch automation triggers: {error}",
                source="TWITCH",
            )

    def register(self, context: ModuleContext) -> None:
        self._context = context
        self._enabled = True

    def configure(self, context: ModuleContext) -> None:
        self._context = context

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._maintenance_timer = QTimer()
        self._maintenance_timer.setInterval(30 * 60 * 1000)
        self._maintenance_timer.timeout.connect(self.auth.maintain)
        self._maintenance_timer.timeout.connect(self.bot_auth.maintain)
        self._maintenance_timer.start()
        self._correlation_timer = QTimer()
        self._correlation_timer.setInterval(100)
        self._correlation_timer.timeout.connect(self.flush_subscription_events)
        self._correlation_timer.start()
        self._channel_snapshot_timer = QTimer()
        self._channel_snapshot_timer.setInterval(60_000)
        if self._refresh_channel_snapshot is not None:
            self._channel_snapshot_timer.timeout.connect(
                self._refresh_channel_snapshot
            )
        self._channel_snapshot_timer.start()
        self._stream_overview_timer = QTimer()
        self._stream_overview_timer.setInterval(1_000)
        if self._update_stream_overview is not None:
            self._stream_overview_timer.timeout.connect(
                self._update_stream_overview
            )
        self._stream_overview_timer.start()
        self.auth.restore()
        self.bot_auth.restore()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for timer in (
            self._maintenance_timer,
            self._correlation_timer,
            self._channel_snapshot_timer,
            self._stream_overview_timer,
        ):
            if timer is not None:
                timer.stop()
                timer.deleteLater()
        self._maintenance_timer = None
        self._correlation_timer = None
        self._channel_snapshot_timer = None
        self._stream_overview_timer = None
        self._subscription_correlator.clear()
        self.service.disconnect()
        self.auth.shutdown()
        self.bot_auth.shutdown()
        self._enabled = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def maintenance_timer(self) -> QTimer | None:
        return self._maintenance_timer

    @property
    def correlation_timer(self) -> QTimer | None:
        return self._correlation_timer

    @property
    def channel_snapshot_timer(self) -> QTimer | None:
        return self._channel_snapshot_timer

    @property
    def stream_overview_timer(self) -> QTimer | None:
        return self._stream_overview_timer

    def observe_activity(self, event: TwitchEvent) -> None:
        if not self._started:
            return
        for automation_event in self._subscription_correlator.observe(event):
            self._publish_event(automation_event)

    def flush_subscription_events(self) -> None:
        if not self._started:
            return
        for event in self._subscription_correlator.flush():
            self._publish_event(event)

    def clear_subscription_events(self) -> None:
        self._subscription_correlator.clear()

    def reconcile_broadcaster_auth(
        self,
        state: TwitchAuthState,
        previous_state: TwitchAuthState,
        channel: str,
    ) -> bool:
        """Apply the existing broadcaster-auth transport policy."""

        if not self._started:
            return False
        if state in {TwitchAuthState.ERROR, TwitchAuthState.SIGNED_OUT}:
            self.service.disconnect()
            return False
        if state is not TwitchAuthState.SIGNED_IN or previous_state is state:
            return False
        target = self.service.channel or channel.strip().lstrip("#").casefold()
        if not target:
            return False
        if self.service.state is TwitchConnectionState.CONNECTED:
            self.service.disconnect()
        return self.service.connect(target)

    def reconcile_bot_auth(
        self,
        state: TwitchAuthState,
        previous_state: TwitchAuthState,
    ) -> bool:
        """Reopen only chat transport when the optional bot identity changes."""

        if (
            not self._started
            or state is previous_state
            or self.auth.token is None
            or not self.service.channel
            or state
            not in {
                TwitchAuthState.SIGNED_IN,
                TwitchAuthState.SIGNED_OUT,
                TwitchAuthState.ERROR,
            }
        ):
            return False
        channel = self.service.channel
        self.service.disconnect()
        return self.service.connect(channel)

    def observe_stream(self, stream: dict[str, object] | None) -> None:
        snapshot = dict(self._channel_snapshot)
        snapshot["stream"] = dict(stream) if stream is not None else None
        self._channel_snapshot = snapshot
        if self.trigger_store is not None:
            self.trigger_store.observe_stream(stream)

    def update_channel_snapshot(self, snapshot: dict[str, object]) -> None:
        self._channel_snapshot = dict(snapshot)

    def variable_values(self) -> dict[str, object]:
        stream = self._channel_snapshot.get("stream")
        stream = stream if isinstance(stream, dict) else {}
        channel = self._channel_snapshot.get("channel")
        channel = channel if isinstance(channel, dict) else {}
        return {
            "channel": self.service.channel or "",
            "title": stream.get("title") or channel.get("title") or "",
            "category": stream.get("game_name") or channel.get("game_name") or "",
            "viewer_count": stream.get("viewer_count"),
            "game_id": stream.get("game_id") or channel.get("game_id") or "",
            "connected": self.service.state is TwitchConnectionState.CONNECTED,
        }

    def publish_first_message(
        self,
        message: TwitchMessage,
        *,
        stream_is_live: bool,
    ) -> None:
        if not self._started or self.trigger_store is None:
            return
        for trigger in self.trigger_store.evaluate_first_message(
            message,
            stream_is_live=stream_is_live,
        ):
            self._publish_trigger(trigger, f"First message from {message.username}")

    def publish_keyword_phrase(self, message: TwitchMessage) -> None:
        if not self._started or self.trigger_store is None:
            return
        for trigger in self.trigger_store.evaluate_keyword_phrase(message):
            self._publish_trigger(trigger, f"Keyword / Phrase match from {message.username}")

    def publish_outgoing_raid_started(self, event: object) -> None:
        if (
            not self._started
            or self.trigger_store is None
            or not isinstance(event, OutgoingRaidStartedEvent)
        ):
            return
        event_context = self.trigger_store.outgoing_raid_started_context(event)
        for trigger in self.trigger_store.evaluate_named(
            OUTGOING_RAID_STARTED_EVENT_TYPE,
            event_context,
            trigger_type="raid_out_started",
        ):
            self._publish_trigger(trigger, "Twitch — Raid Out Started")

    def publish_ads_event(self, event: AdsDomainEvent) -> None:
        if not self._started or self.trigger_store is None:
            return
        for trigger in self.trigger_store.evaluate_named(
            event.event_type,
            event.context,
            trigger_type="ads",
        ):
            self._publish_trigger(trigger, event.event_type.replace(".", " ").title())

    def _publish_event(self, event: TwitchEvent) -> None:
        if self.trigger_store is None:
            return
        for trigger in self.trigger_store.evaluate(event):
            self._publish_trigger(trigger, f"Twitch event {event.subscription_type}")

    def _publish_trigger(self, trigger: TriggerEvent, label: str) -> None:
        context = self._context
        if not self._started or context is None or context.automation is None:
            return
        execution = context.automation.publish_trigger(trigger)
        if context.execution_recorder is not None:
            context.execution_recorder(execution, label)
        if execution.succeeded:
            Logger.info(f'Executed Twitch automation for "{label}".', source="AUTOMATION")
        elif execution.handled:
            Logger.warning(f'Twitch automation failed for "{label}".', source="AUTOMATION")
