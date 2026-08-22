from __future__ import annotations

from collections.abc import Callable, Mapping

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.variable_registry import (
    CallbackVariableProvider,
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
    VariableSnapshot,
)
from products.hub.counters.service import CounterService


class CustomVariableProvider:
    source = "Custom"

    def __init__(self, store: CustomVariableStore) -> None:
        self.store = store

    def definitions(self) -> tuple[VariableDefinition, ...]:
        return tuple(
            VariableDefinition(
                name=f"custom.{name}",
                display_name=f"Custom - {name.replace('_', ' ').title()}",
                description=self.store.description_of(name),
                data_type=VariableDataType(self.store.type_of(name)),
                source=self.source,
                category="Custom",
                writable=True,
            )
            for name in sorted(self.store.values())
        )

    def resolve(self, name: str, context: Mapping[str, object]) -> VariableSnapshot:
        bare = self.store.validate_custom_name(name)
        definition = next(item for item in self.definitions() if item.name == name)
        values = self.store.values()
        return VariableSnapshot(
            definition,
            values.get(bare),
            bare in values,
            "" if bare in values else "Custom variable is unavailable.",
        )

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        bare = self.store.validate_custom_name(name)
        scope = self.store.scope_of(bare) or "global"
        self.store.set(scope, bare, value)
        return self.resolve(f"custom.{bare}", {})


class CounterVariableProvider:
    source = "Counters"

    def __init__(
        self,
        service: CounterService,
        stream_id: Callable[[], str] | None = None,
    ) -> None:
        self.service = service
        self.stream_id = stream_id or (lambda: "")

    def definitions(self) -> tuple[VariableDefinition, ...]:
        definitions: list[VariableDefinition] = []
        for counter in self.service.list_counters():
            stream_name = f"counter.{counter.counter_id}.stream"
            definitions.extend(
                (
                    VariableDefinition(
                        name=stream_name,
                        display_name=f"{counter.display_name} - Stream",
                        description=f"Shared channel counter value for {counter.display_name}.",
                        data_type=(
                            VariableDataType.INTEGER
                            if counter.numeric_type == "integer"
                            else VariableDataType.NUMBER
                        ),
                        source=self.source,
                        category="Counters",
                        writable=counter.enabled and counter.track_channel_total,
                    ),
                    VariableDefinition(
                        name=f"counter.{counter.counter_id}.viewer",
                        display_name=f"{counter.display_name} - Viewer",
                        description=(
                            f"Lifetime value for {counter.display_name} associated "
                            "with the current viewer."
                        ),
                        data_type=(
                            VariableDataType.INTEGER
                            if counter.numeric_type == "integer"
                            else VariableDataType.NUMBER
                        ),
                        source=self.source,
                        category="Counters",
                        availability=VariableAvailability.CONTEXTUAL,
                        writable=False,
                        required_context=("user.id",),
                    ),
                )
            )
        return tuple(definitions)

    def resolve(self, name: str, context: Mapping[str, object]) -> VariableSnapshot:
        counter_id, scope = self._parts(name)
        definition = next(item for item in self.definitions() if item.name == name)
        counter = self.service.get_counter(counter_id)
        tracked = counter and (
            counter.track_channel_total
            if scope == "stream"
            else counter.track_viewer_total
        )
        if counter is None or not counter.enabled or not tracked:
            return VariableSnapshot(definition, None, False, "Counter is unavailable.")
        user_id = self._context_user_id(context)
        if scope == "viewer" and not user_id:
            return VariableSnapshot(definition, None, False, "Requires viewer context.")
        values = self.service.get_values(
            counter_id,
            user_id=user_id,
            stream_id=self.stream_id(),
        )
        value = values.channel_total if scope == "stream" else values.viewer_total
        return VariableSnapshot(definition, value, True)

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        counter_id, scope = self._parts(name)
        if scope != "stream":
            raise PermissionError(f'Variable "{name}" is read-only.')
        result = self.service.set_value(counter_id, "channel_total", value)
        if result.status not in {"success", "minimum_reached"}:
            raise ValueError(result.detail or f"Counter update failed: {result.status}.")
        return self.resolve(name, {})

    @staticmethod
    def _parts(name: str) -> tuple[str, str]:
        prefix, counter_id, scope = name.split(".", 2)
        if prefix != "counter" or scope not in {"stream", "viewer"}:
            raise KeyError(f'Unknown counter variable "{name}".')
        return counter_id, scope

    @staticmethod
    def _context_user_id(context: Mapping[str, object]) -> str:
        for key in ("user.id", "user_id", "viewer_id"):
            value = str(context.get(key, "")).strip()
            if value not in {"", "--"}:
                return value
        return ""


class AdsVariableProvider:
    source = "Twitch Ads"

    _FIELDS = (
        ("next_at", "Next Ad At", "Scheduled start time of the next automatic ad.", VariableDataType.DATETIME),
        ("next_in", "Next Ad In", "Seconds until the next scheduled automatic ad.", VariableDataType.INTEGER),
        ("next_duration", "Next Ad Duration", "Scheduled duration of the next automatic ad in seconds.", VariableDataType.INTEGER),
        ("last_at", "Last Ad At", "Start time of the most recently observed ad.", VariableDataType.DATETIME),
        ("snooze_count", "Ad Snoozes", "Currently available Twitch ad snoozes.", VariableDataType.INTEGER),
        ("snooze_refresh_at", "Next Snooze At", "Time Twitch will restore a snooze.", VariableDataType.DATETIME),
        ("snooze_refresh_in", "Next Snooze In", "Seconds until Twitch restores a snooze.", VariableDataType.INTEGER),
        ("preroll_free_time", "Preroll-Free Time", "Seconds of Twitch preroll-free time remaining.", VariableDataType.INTEGER),
        ("in_progress", "Ads In Progress", "Whether Hub currently considers an ad break active.", VariableDataType.BOOLEAN),
        ("remaining", "Ad Time Remaining", "Estimated seconds remaining in the active ad break.", VariableDataType.INTEGER),
        ("duration", "Active Ad Duration", "Duration of the active ad break in seconds.", VariableDataType.INTEGER),
        ("started_at", "Ad Started At", "Start time reported for the active ad break.", VariableDataType.DATETIME),
        ("is_automatic", "Ad Is Automatic", "Whether Twitch reported the active ad as automatic.", VariableDataType.BOOLEAN),
        ("manual_retry_after", "Manual Ad Retry", "Seconds until another manual commercial can be requested.", VariableDataType.INTEGER),
    )

    def __init__(self, values: Callable[[], Mapping[str, object]]) -> None:
        self.values = values

    def definitions(self) -> tuple[VariableDefinition, ...]:
        return tuple(
            VariableDefinition(
                name=f"ads.{name}",
                display_name=display_name,
                description=description,
                data_type=data_type,
                source=self.source,
                category="Ads",
                preview_value={
                    "next_at": "2026-08-22T20:00:00+00:00",
                    "next_in": 300,
                    "next_duration": 90,
                    "last_at": "2026-08-22T19:30:00+00:00",
                    "snooze_count": 2,
                    "snooze_refresh_at": "2026-08-22T20:15:00+00:00",
                    "snooze_refresh_in": 900,
                    "preroll_free_time": 1200,
                    "in_progress": False,
                    "remaining": 45,
                    "duration": 90,
                    "started_at": "2026-08-22T19:58:30+00:00",
                    "is_automatic": True,
                    "manual_retry_after": 0,
                }.get(name),
            )
            for name, display_name, description, data_type in self._FIELDS
        )

    def resolve(self, name: str, _context: Mapping[str, object]) -> VariableSnapshot:
        definition = next(item for item in self.definitions() if item.name == name)
        key = name.split(".", 1)[1]
        value = self.values().get(key)
        return VariableSnapshot(
            definition,
            value,
            value is not None,
            "" if value is not None else "Twitch ad state is unavailable.",
        )

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        raise PermissionError(f'Variable "{name}" is read-only.')


CONTEXT_DEFINITIONS = (
    ("user.name", "User Name", "Triggering user's readable name.", VariableDataType.TEXT, ("user",)),
    ("user.display_name", "User Display Name", "Triggering user's Twitch display name.", VariableDataType.TEXT, ("user",)),
    ("user.id", "User ID", "Triggering user's Twitch ID.", VariableDataType.TEXT, ("user_id",)),
    ("user.login", "User Login", "Triggering viewer's Twitch login.", VariableDataType.TEXT, ("user_login",)),
    ("user.permission", "User Permission", "Triggering viewer's command permission.", VariableDataType.TEXT, ("viewer_permission",)),
    ("user.is_mod", "User Is Moderator", "Whether the triggering user is a moderator.", VariableDataType.BOOLEAN, ("user_is_mod", "is_mod")),
    ("user.is_subscriber", "User Is Subscriber", "Whether the triggering user is subscribed.", VariableDataType.BOOLEAN, ("user_is_subscriber", "is_subscriber")),
    ("chat.message", "Chat Message", "Triggering Twitch chat message.", VariableDataType.TEXT, ("message",)),
    ("chat.message_id", "Chat Message ID", "Triggering Twitch message ID.", VariableDataType.TEXT, ("message_id",)),
    ("command.name", "Command Name", "Command name without the exclamation mark.", VariableDataType.TEXT, ("command",)),
    ("command.data", "Command Data", "Raw text following the command name, trimmed at its outer edges.", VariableDataType.TEXT, ("command_data",)),
    ("command.target", "Command Target", "First command argument without the at sign.", VariableDataType.TEXT, ("target",)),
    ("command.uses", "Command Uses", "Number of times the command has run.", VariableDataType.INTEGER, ("uses",)),
    ("keyword.message", "Keyword Message", "Full Twitch message that matched the Keyword / Phrase trigger.", VariableDataType.TEXT, ("keyword.message",)),
    ("keyword.match", "Keyword Match", "Configured keyword or phrase that matched.", VariableDataType.TEXT, ("keyword.match",)),
    ("keyword.before", "Text Before Keyword", "Text before the matched keyword or phrase.", VariableDataType.TEXT, ("keyword.before",)),
    ("keyword.after", "Text After Keyword", "Text after the matched keyword or phrase.", VariableDataType.TEXT, ("keyword.after",)),
    ("ads.requester.id", "Ad Requester ID", "Twitch ID of the requester reported for an Ads Started event.", VariableDataType.TEXT, ("ads.requester.id",)),
    ("ads.requester.name", "Ad Requester Name", "Readable requester name reported for an Ads Started event.", VariableDataType.TEXT, ("ads.requester.name",)),
    ("event.name", "Event Name", "Readable trigger event name.", VariableDataType.TEXT, ("event",)),
    ("event.type", "Event Type", "Owning service event type.", VariableDataType.TEXT, ("event_type",)),
    ("event.input", "Event Input", "Viewer input or event input name.", VariableDataType.TEXT, ("input",)),
    ("event.amount", "Event Amount", "Event amount, cost, bits, or viewers.", VariableDataType.NUMBER, ("amount",)),
    ("event.bits", "Cheered Bits", "Number of cheered bits.", VariableDataType.INTEGER, ("bits",)),
    ("event.viewers", "Event Viewers", "Viewer or raid count.", VariableDataType.INTEGER, ("viewers",)),
    ("event.tier", "Subscription Tier", "Twitch subscription tier.", VariableDataType.TEXT, ("tier",)),
    ("event.reward", "Reward Title", "Channel-point reward title.", VariableDataType.TEXT, ("reward",)),
    ("event.reward_id", "Reward ID", "Channel-point reward ID.", VariableDataType.TEXT, ("reward_id",)),
    ("event.reward_cost", "Reward Cost", "Channel-point reward cost.", VariableDataType.INTEGER, ("reward_cost",)),
    ("event.redemption_id", "Redemption ID", "Channel-point redemption ID.", VariableDataType.TEXT, ("redemption_id",)),
    ("obs.scene", "OBS Scene", "Scene supplied by an OBS event.", VariableDataType.TEXT, ("scene",)),
    ("obs.source", "OBS Source", "Source supplied by an OBS event.", VariableDataType.TEXT, ("source",)),
    ("obs.input", "OBS Input", "Input supplied by an OBS event.", VariableDataType.TEXT, ("input",)),
    ("obs.output_state", "OBS Output State", "Output state supplied by OBS.", VariableDataType.TEXT, ("output_state",)),
    ("obs.enabled", "OBS Enabled", "Whether the OBS source or mode is enabled.", VariableDataType.BOOLEAN, ("enabled",)),
    ("obs.muted", "OBS Muted", "Whether the OBS input is muted.", VariableDataType.BOOLEAN, ("muted", "mute")),
    ("obs.volume_db", "OBS Volume", "OBS input volume in decibels.", VariableDataType.NUMBER, ("volume_db",)),
    ("obs.media", "OBS Media", "Media input supplied by OBS.", VariableDataType.TEXT, ("media",)),
)

CONTEXT_PREVIEW = {
    "user.name": "TestViewer", "user.display_name": "TestViewer",
    "user.id": "123456", "user.login": "testviewer", "user.permission": "everyone",
    "user.is_mod": False, "user.is_subscriber": False,
    "chat.message": "Hello Streamhouse!", "chat.message_id": "message-123",
    "command.name": "hello", "command.data": "friend", "command.target": "friend", "command.uses": 3,
    "keyword.message": "I think coffee is better than tea", "keyword.match": "coffee",
    "keyword.before": "I think", "keyword.after": "is better than tea",
    "ads.requester.id": "123456", "ads.requester.name": "Streamer",
    "event.name": "Follow", "event.type": "channel.follow", "event.input": "Viewer input",
    "event.amount": 100, "event.bits": 100, "event.viewers": 12, "event.tier": "1000",
    "event.reward": "Hydrate", "event.reward_id": "reward-123", "event.reward_cost": 500,
    "event.redemption_id": "redemption-123", "obs.scene": "Gameplay", "obs.source": "Camera",
    "obs.input": "Microphone", "obs.output_state": "OBS_WEBSOCKET_OUTPUT_STARTED",
    "obs.enabled": True, "obs.muted": False, "obs.volume_db": -8.0, "obs.media": "Intro Video",
}


def context_provider() -> CallbackVariableProvider:
    definitions = tuple(
        VariableDefinition(
            name=name,
            display_name=display,
            description=description,
            data_type=data_type,
            source="Twitch Context",
            category=name.split(".", 1)[0].title(),
            availability=VariableAvailability.CONTEXTUAL,
            required_context=tuple(_aliases),
            preview_value=CONTEXT_PREVIEW.get(name),
        )
        for name, display, description, data_type, _aliases in CONTEXT_DEFINITIONS
    )
    aliases = {name: values for name, *_rest, values in CONTEXT_DEFINITIONS}

    def resolve(name: str, context: Mapping[str, object]) -> tuple[bool, object, str]:
        for key in (name, *aliases[name]):
            if key in context and str(context[key]).strip() not in {"", "--"}:
                value: object = context[key]
                if name in {"user.is_mod", "user.is_subscriber", "obs.enabled", "obs.muted"}:
                    value = str(value).strip().casefold() in {"1", "true", "yes", "on"}
                return True, value, ""
        return False, None, "Only available during a matching trigger event."

    return CallbackVariableProvider("Twitch Context", definitions, resolve)


def runtime_provider(
    twitch_values: Callable[[], Mapping[str, object]],
    *,
    obs_connected: Callable[[], bool],
    obs_scene: Callable[[], str],
    hub_uptime: Callable[[], object],
) -> CallbackVariableProvider:
    definitions = (
        VariableDefinition("stream.title", "Stream Title", "Current cached Twitch title.", VariableDataType.TEXT, "Twitch", "Stream"),
        VariableDefinition("stream.channel", "Stream Channel", "Connected Twitch channel name.", VariableDataType.TEXT, "Twitch", "Stream"),
        VariableDefinition("stream.category", "Stream Category", "Current cached Twitch category.", VariableDataType.TEXT, "Twitch", "Stream"),
        VariableDefinition("stream.viewer_count", "Viewer Count", "Current cached live viewer count.", VariableDataType.INTEGER, "Twitch", "Stream"),
        VariableDefinition("stream.game_id", "Twitch Game ID", "Current cached Twitch category ID.", VariableDataType.TEXT, "Twitch", "Stream"),
        VariableDefinition("obs.current_scene", "Current OBS Scene", "Most recently observed OBS program scene.", VariableDataType.TEXT, "OBS", "OBS"),
        VariableDefinition("hub.uptime", "Hub Uptime", "Time since this Hub process started.", VariableDataType.TEXT, "Hub", "Hub"),
        VariableDefinition("hub.connected_to_twitch", "Twitch Connected", "Whether Hub is connected to Twitch.", VariableDataType.BOOLEAN, "Hub", "Hub"),
        VariableDefinition("hub.connected_to_obs", "OBS Connected", "Whether Hub is connected to OBS.", VariableDataType.BOOLEAN, "Hub", "Hub"),
    )

    def resolve(name: str, _context: Mapping[str, object]) -> tuple[bool, object, str]:
        twitch = twitch_values()
        if name.startswith("stream."):
            key = name.split(".", 1)[1]
            value = twitch.get(key)
            return value not in {None, "", "--"}, value, "No cached Twitch value is available."
        if name == "obs.current_scene":
            value = obs_scene()
            return (
                obs_connected() and bool(value),
                value,
                "OBS is disconnected or no program scene has been observed yet.",
            )
        if name == "hub.uptime":
            return True, hub_uptime(), ""
        if name == "hub.connected_to_twitch":
            return True, bool(twitch.get("connected", False)), ""
        if name == "hub.connected_to_obs":
            return True, obs_connected(), ""
        return False, None, "Unavailable."

    return CallbackVariableProvider("Hub Runtime", definitions, resolve)
