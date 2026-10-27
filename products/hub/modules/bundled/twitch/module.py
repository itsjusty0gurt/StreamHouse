from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer

from products.hub.automation.models import TriggerEvent
from products.hub.automation.task_catalog import TWITCH_TASK_METADATA
from products.hub.automation.variable_providers import twitch_context_provider
from products.hub.config.twitch import TWITCH_BOT_SCOPES
from products.hub.core.events import Events
from products.hub.modules.contracts import Availability, ModuleContext, ModuleDescriptor
from products.hub.modules.contributions import (
    AutomationSubpageContribution,
    BackupContributor,
    ModuleTabContribution,
    TriggerProviderContribution,
)
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
from products.hub.twitch.channel_information import ChannelInformationStore
from products.hub.twitch.chatter_history import (
    BACKUP_CHATTER_FIELDS,
    ChatterHistoryStore,
)
from products.hub.twitch.command_worker import (
    CommandExecutionWorker,
    CommandExecutionWorkerResult,
)
from products.hub.twitch.commands import (
    TwitchCommandTriggerDispatcher,
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerStore,
)
from products.hub.twitch.tasks import (
    TWITCH_INFORMATION_TASK_TYPES,
    TWITCH_TASK_LABELS,
    register_twitch_tasks,
)
from products.hub.twitch.token_store import TwitchTokenStore
from shared.streamhouse_runtime.json_store import (
    UnsupportedJsonSchemaError,
    json_store_exists,
    load_validated_json,
)
from shared.streamhouse_runtime.logger import Logger


TWITCH_MODULE_ID = "streamhouse.twitch"
TWITCH_TRIGGER_PROVIDER_ID = "twitch.automation"
TWITCH_COMMANDS_BACKUP_ID = "twitch.commands"
TWITCH_CHANNEL_INFORMATION_BACKUP_ID = "twitch.channel_information"
TWITCH_TRIGGERS_BACKUP_ID = "twitch.triggers"
TWITCH_USERS_BACKUP_ID = "twitch.users"


class TwitchModule:
    """First-party Twitch catalog and live runtime lifecycle owner."""

    descriptor = ModuleDescriptor(
        module_id=TWITCH_MODULE_ID,
        display_name="Twitch",
        version="0.3.0-alpha",
        category="Streaming Platform",
        enabled_by_default=True,
        required_core_capabilities=frozenset(
            {
                "automation",
                "automation_pages",
                "backup",
                "tabs",
                "tasks",
                "triggers",
                "variables",
            }
        ),
        capabilities=frozenset(
            {
                "twitch.automation",
                "twitch.commands",
                "twitch.backup",
                "twitch.ui",
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
        self.command_store: TwitchCommandTriggerStore | None = None
        self.command_dispatcher: TwitchCommandTriggerDispatcher | None = None
        self.channel_information_store: ChannelInformationStore | None = None
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
        self._workspace_factory: Callable[[object], object] | None = None
        self._commands_page_factory: Callable[[object], object] | None = None
        self._command_thread_pool = QThreadPool()
        self._command_thread_pool.setMaxThreadCount(1)
        self._command_workers: set[CommandExecutionWorker] = set()

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

    def bind_command_store(self, store: TwitchCommandTriggerStore) -> None:
        if self._catalog_registered:
            raise RuntimeError("The Twitch command catalog is already registered.")
        self.command_store = store

    def bind_channel_information_store(self, store: ChannelInformationStore) -> None:
        if self._catalog_registered:
            raise RuntimeError("The Twitch data catalog is already registered.")
        self.channel_information_store = store

    def ensure_data_stores(self, data_root: Path, routine_store: object) -> None:
        """Create Twitch-owned public stores at their existing Alpha paths."""

        if self.command_store is None:
            self.command_store = TwitchCommandTriggerStore(
                data_root / "twitch" / "commands.json",
                routine_store,
            )
        if self.channel_information_store is None:
            self.channel_information_store = ChannelInformationStore(
                data_root / "twitch" / "channel-information.json"
            )
        if self.command_dispatcher is None:
            self.command_dispatcher = TwitchCommandTriggerDispatcher(
                self.command_store,
                channel_information=self.channel_information_store,
            )

    def bind_ui_factories(
        self,
        *,
        workspace: Callable[[object], object],
        commands: Callable[[object], object],
    ) -> None:
        if self._catalog_registered:
            raise RuntimeError("Twitch UI factories must be bound before registration.")
        self._workspace_factory = workspace
        self._commands_page_factory = commands

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
        self.ensure_data_stores(data_root, context.routine_store)
        assert self.command_store is not None
        assert self.channel_information_store is not None
        self.command_store.variable_registry = context.variables
        self.command_store.load_for_startup()
        self.channel_information_store.load_for_startup()
        register_twitch_tasks(
            context.tasks,
            self.service,
            context.variable_resolver,
            lambda: self.command_store,
            lambda: self.channel_information_store,
            context.variables,
            self.ads_service,
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
        if context.automation_pages is not None:
            if self._commands_page_factory is None:
                raise ValueError("The Twitch Commands page factory is unavailable.")
            context.automation_pages.register(
                AutomationSubpageContribution(
                    page_id="twitch.commands",
                    owner_module_id=TWITCH_MODULE_ID,
                    label="Commands",
                    order=450,
                    factory=self._commands_page_factory,
                    availability=self.availability,
                )
            )
        if context.backup is not None:
            context.backup.register(
                BackupContributor(
                    contributor_id=TWITCH_COMMANDS_BACKUP_ID,
                    owner_module_id=TWITCH_MODULE_ID,
                    display_name="Commands",
                    schema_version=TwitchCommandTriggerStore.VERSION,
                    export=self._backup_commands_export,
                    validate=self._backup_commands_validate,
                )
            )
            context.backup.register(
                BackupContributor(
                    contributor_id=TWITCH_CHANNEL_INFORMATION_BACKUP_ID,
                    owner_module_id=TWITCH_MODULE_ID,
                    display_name="Channel Information",
                    schema_version=ChannelInformationStore.VERSION,
                    export=self._backup_channel_information_export,
                    validate=self._backup_channel_information_validate,
                )
            )
            context.backup.register(
                BackupContributor(
                    contributor_id=TWITCH_TRIGGERS_BACKUP_ID,
                    owner_module_id=TWITCH_MODULE_ID,
                    display_name="Twitch Triggers",
                    schema_version=TwitchEventTriggerStore.VERSION,
                    export=self._backup_triggers_export,
                    validate=self._backup_triggers_validate,
                )
            )
            context.backup.register(
                BackupContributor(
                    contributor_id=TWITCH_USERS_BACKUP_ID,
                    owner_module_id=TWITCH_MODULE_ID,
                    display_name="Twitch Users",
                    schema_version=ChatterHistoryStore.VERSION,
                    export=self._backup_users_export,
                    validate=self._backup_users_validate,
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
        if context.tabs is not None:
            if self._workspace_factory is None:
                raise ValueError("The Twitch workspace factory is unavailable.")
            context.tabs.register(
                ModuleTabContribution(
                    tab_id="twitch.workspace",
                    owner_module_id=TWITCH_MODULE_ID,
                    label="Twitch",
                    order=250,
                    factory=self._workspace_factory,
                )
            )

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
        self._command_thread_pool.clear()
        self._command_thread_pool.waitForDone(2_000)
        self._command_workers.clear()
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

    def wait_for_commands(self, timeout_ms: int = 2_000) -> bool:
        return self._command_thread_pool.waitForDone(timeout_ms)

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

    def handle_command(
        self,
        message: TwitchMessage,
        *,
        is_bot: bool,
        command_context: Mapping[str, str],
    ) -> bool:
        if is_bot or not self._started or self.command_dispatcher is None:
            return False
        result = self.command_dispatcher.evaluate(message, command_context)
        if not result.handled:
            return False
        if result.outcome is not TwitchCommandTriggerOutcome.READY:
            Events.emit(
                "twitch_command_trigger_rejected",
                trigger_id=result.trigger_id,
                invocation=result.invocation,
                outcome=result.outcome.value,
                remaining_seconds=result.remaining_seconds,
            )
            return True
        assert self.command_store is not None
        routine = self.command_store.routine_store.get(result.routine_id)
        context = self._context
        automation = context.automation if context is not None else None
        command_automation = context.command_automation if context is not None else None
        if automation is None:
            return False
        if routine is not None and any(
            task.enabled and task.task_type in TWITCH_INFORMATION_TASK_TYPES
            for task in routine.tasks
        ):
            worker = CommandExecutionWorker(command_automation or automation, result, message)
            self._command_workers.add(worker)
            worker.signals.completed.connect(
                lambda outcome, current=worker: self._command_worker_completed(
                    current, outcome
                )
            )
            worker.signals.failed.connect(
                lambda command, current_message, error, current=worker: self._command_worker_failed(
                    current, command, current_message, error
                )
            )
            self._command_thread_pool.start(worker)
            return True
        execution = automation.publish_trigger(result.to_event())
        self._complete_command_execution(result, message, execution)
        return True

    def _command_worker_completed(
        self,
        worker: CommandExecutionWorker,
        outcome: CommandExecutionWorkerResult,
    ) -> None:
        self._command_workers.discard(worker)
        if self._started:
            self._complete_command_execution(
                outcome.command,
                outcome.message,
                outcome.execution,
            )

    def _command_worker_failed(
        self,
        worker: CommandExecutionWorker,
        result: object,
        _message: TwitchMessage,
        _error: str,
    ) -> None:
        self._command_workers.discard(worker)
        if not self._started:
            return
        invocation = str(getattr(result, "invocation", ""))
        Logger.warning(f"Twitch command !{invocation} failed.", source="TWITCH")
        Events.emit(
            "twitch_command_trigger_rejected",
            trigger_id=str(getattr(result, "trigger_id", "")),
            invocation=invocation,
            outcome=TwitchCommandTriggerOutcome.TASK_FAILED.value,
            remaining_seconds=0,
        )

    def _complete_command_execution(
        self,
        result: object,
        message: TwitchMessage,
        execution: object,
    ) -> None:
        context = self._context
        if context is None or not self._started or self.command_dispatcher is None:
            return
        invocation = str(getattr(result, "invocation", ""))
        if context.execution_recorder is not None:
            context.execution_recorder(execution, f"Twitch command !{invocation}")
        if bool(getattr(execution, "succeeded", False)):
            try:
                self.command_dispatcher.record_executed(result, message)
            except OSError as error:
                Logger.warning(
                    f"Command was sent but its statistics could not be saved: {error}",
                    source="TWITCH",
                )
            if context.commands_changed is not None:
                context.commands_changed(str(getattr(result, "trigger_id", "")))
            Events.emit(
                "twitch_command_trigger_executed",
                trigger_id=str(getattr(result, "trigger_id", "")),
                invocation=invocation,
            )
            Logger.info(f"Executed custom Twitch command !{invocation}.", source="TWITCH")
        else:
            Events.emit(
                "twitch_command_trigger_rejected",
                trigger_id=str(getattr(result, "trigger_id", "")),
                invocation=invocation,
                outcome=TwitchCommandTriggerOutcome.TASK_FAILED.value,
                remaining_seconds=0,
            )
            Logger.warning(f"Twitch command !{invocation} failed.", source="TWITCH")

    def _backup_commands_export(self, project_root: object) -> Mapping[str, object]:
        assert self.command_store is not None
        path = Path(project_root) / "twitch" / "commands.json"
        if json_store_exists(path):
            payload = load_validated_json(path, self.command_store._parse_payload)
            return {
                "version": TwitchCommandTriggerStore.VERSION,
                "triggers": [asdict(item) for item in payload],
            }
        return {"version": TwitchCommandTriggerStore.VERSION, "triggers": []}

    def _backup_commands_validate(self, payload: object) -> None:
        assert self.command_store is not None
        self.command_store._parse_payload(payload)

    def _backup_channel_information_export(
        self,
        project_root: object,
    ) -> Mapping[str, object]:
        assert self.channel_information_store is not None
        path = Path(project_root) / "twitch" / "channel-information.json"
        information = (
            load_validated_json(path, self.channel_information_store._parse_payload)
            if json_store_exists(path)
            else self.channel_information_store.snapshot()
        )
        return {
            "version": ChannelInformationStore.VERSION,
            **asdict(information),
        }

    def _backup_channel_information_validate(self, payload: object) -> None:
        assert self.channel_information_store is not None
        self.channel_information_store._parse_payload(payload)

    def _backup_triggers_export(self, project_root: object) -> Mapping[str, object]:
        assert self.trigger_store is not None
        path = Path(project_root) / "twitch" / "event_triggers.json"
        payload: object
        if json_store_exists(path):
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = {
                "version": TwitchEventTriggerStore.VERSION,
                "first_message": {
                    "raid_suppression_enabled": True,
                    "raid_suppression_minutes": 3,
                },
                "triggers": [],
            }
        self._backup_triggers_validate(payload)
        assert isinstance(payload, Mapping)
        return payload

    def _backup_triggers_validate(self, payload: object) -> None:
        assert self.trigger_store is not None
        self.trigger_store._parse_payload(payload)

    def _backup_users_export(self, project_root: object) -> Mapping[str, object]:
        path = Path(project_root) / "memory" / "twitch_chatters.json"
        payload: object = (
            json.loads(path.read_text(encoding="utf-8"))
            if json_store_exists(path)
            else {"version": ChatterHistoryStore.VERSION, "chatters": {}}
        )
        self._backup_users_validate(payload)
        assert isinstance(payload, Mapping)
        records = payload.get("chatters", {})
        assert isinstance(records, Mapping)
        return {
            "version": ChatterHistoryStore.VERSION,
            "chatters": {
                str(user_id): {
                    key: value
                    for key, value in record.items()
                    if key in BACKUP_CHATTER_FIELDS
                }
                for user_id, record in records.items()
                if str(user_id).strip() and isinstance(record, Mapping)
            },
        }

    @staticmethod
    def _backup_users_validate(payload: object) -> None:
        if not isinstance(payload, Mapping):
            raise ValueError("Twitch Users must contain a JSON object.")
        if payload.get("version") != ChatterHistoryStore.VERSION:
            raise UnsupportedJsonSchemaError(
                "Twitch Users uses an unsupported schema version."
            )
        ChatterHistoryStore()._parse_payload(payload)

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
