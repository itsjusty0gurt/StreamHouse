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


RESERVED_NAMESPACES = frozenset(
    {"stream", "user", "chat", "counter", "obs", "hub", "automation"}
)


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
        return tuple(
            VariableDefinition(
                name=f"counter.{counter.counter_id}",
                display_name=counter.display_name,
                description=f"Channel all-time total for {counter.display_name}.",
                data_type=VariableDataType.INTEGER,
                source=self.source,
                category="Counters",
                writable=counter.enabled and counter.track_channel_total,
            )
            for counter in self.service.list_counters()
        )

    def resolve(self, name: str, context: Mapping[str, object]) -> VariableSnapshot:
        counter_id = name.removeprefix("counter.")
        definition = next(item for item in self.definitions() if item.name == name)
        counter = self.service.get_counter(counter_id)
        if counter is None or not counter.enabled or not counter.track_channel_total:
            return VariableSnapshot(definition, None, False, "Counter is unavailable.")
        values = self.service.get_values(counter_id, stream_id=self.stream_id())
        return VariableSnapshot(definition, values.channel_total, True)

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        counter_id = name.removeprefix("counter.")
        result = self.service.set_value(counter_id, "channel_total", int(value))
        if result.status not in {"success", "minimum_reached"}:
            raise ValueError(result.detail or f"Counter update failed: {result.status}.")
        return self.resolve(name, {})


CONTEXT_DEFINITIONS = (
    ("user.name", "User Name", "Triggering user's readable name.", VariableDataType.TEXT, ("user",)),
    ("user.display_name", "User Display Name", "Triggering user's Twitch display name.", VariableDataType.TEXT, ("user",)),
    ("user.id", "User ID", "Triggering user's Twitch ID.", VariableDataType.TEXT, ("user_id",)),
    ("user.is_mod", "User Is Moderator", "Whether the triggering user is a moderator.", VariableDataType.BOOLEAN, ("user_is_mod", "is_mod")),
    ("user.is_subscriber", "User Is Subscriber", "Whether the triggering user is subscribed.", VariableDataType.BOOLEAN, ("user_is_subscriber", "is_subscriber")),
    ("chat.message", "Chat Message", "Triggering Twitch chat message.", VariableDataType.TEXT, ("message",)),
    ("chat.message_id", "Chat Message ID", "Triggering Twitch message ID.", VariableDataType.TEXT, ("message_id",)),
)


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
        )
        for name, display, description, data_type, _aliases in CONTEXT_DEFINITIONS
    )
    aliases = {name: values for name, *_rest, values in CONTEXT_DEFINITIONS}

    def resolve(name: str, context: Mapping[str, object]) -> tuple[bool, object, str]:
        for key in (name, *aliases[name]):
            if key in context and str(context[key]).strip() not in {"", "--"}:
                value: object = context[key]
                if name in {"user.is_mod", "user.is_subscriber"}:
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
