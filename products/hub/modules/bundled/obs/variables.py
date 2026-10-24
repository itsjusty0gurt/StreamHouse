from __future__ import annotations

from collections.abc import Callable, Mapping

from products.hub.automation.variable_registry import (
    VariableDataType,
    VariableDefinition,
    VariableProvider,
    VariableSnapshot,
)


class ObsVariableProvider(VariableProvider):
    """Canonical OBS live values backed by the module-owned service."""

    _DEFINITIONS = (
        VariableDefinition(
            "obs.current_scene",
            "Current OBS Scene",
            "Most recently observed OBS program scene.",
            VariableDataType.TEXT,
            "OBS",
            "OBS",
        ),
        VariableDefinition(
            "hub.connected_to_obs",
            "OBS Connected",
            "Whether Hub is connected to OBS.",
            VariableDataType.BOOLEAN,
            "Hub",
            "Hub",
        ),
    )
    source = "OBS"

    def __init__(
        self,
        *,
        available: Callable[[], bool],
        connected: Callable[[], bool],
        scene: Callable[[], str],
    ) -> None:
        self._available = available
        self._connected = connected
        self._scene = scene

    def definitions(self) -> tuple[VariableDefinition, ...]:
        return self._DEFINITIONS

    def resolve(
        self,
        name: str,
        _context: Mapping[str, object],
    ) -> VariableSnapshot:
        definition = next(item for item in self._DEFINITIONS if item.name == name)
        if not self._available():
            return VariableSnapshot(
                definition,
                None,
                False,
                "The OBS module is disabled or unavailable.",
            )
        connected = self._connected()
        if name == "hub.connected_to_obs":
            return VariableSnapshot(definition, connected, True, "")
        scene = self._scene()
        return VariableSnapshot(
            definition,
            scene,
            connected and bool(scene),
            "OBS is disconnected or no program scene has been observed yet.",
        )

    def set_value(self, name: str, value: object) -> VariableSnapshot:
        raise PermissionError(f'Variable "{name}" is read-only.')
