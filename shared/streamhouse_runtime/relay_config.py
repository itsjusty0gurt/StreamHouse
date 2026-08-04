"""Canonical configuration names for the Streamhouse soundboard relay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


RELAY_COMPATIBILITY_VERSION = "relay-compat-v1"
RELAY_COMPATIBILITY_REMOVE_AFTER = "0.3.0"
STREAMHOUSE_RELAY_HOSTNAME = "streamhouse-soundboard-relay.onrender.com"
STREAMHOUSE_RELAY_BASE_DEFAULT = f"https://{STREAMHOUSE_RELAY_HOSTNAME}"
LEGACY_RELAY_HOSTNAME = "sally-soundboard-relay.onrender.com"
LEGACY_RELAY_BASE_DEFAULT = f"https://{LEGACY_RELAY_HOSTNAME}"


@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentValue:
    """A resolved value plus non-sensitive compatibility metadata."""

    name: str
    legacy_name: str
    value: str = field(repr=False)
    source: str
    used_legacy: bool = False
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class RelayEnvironment:
    base: ResolvedEnvironmentValue
    keys: ResolvedEnvironmentValue
    database: ResolvedEnvironmentValue


def resolve_environment_value(
    environment: Mapping[str, str],
    name: str,
    legacy_name: str,
    *,
    default: str = "",
) -> ResolvedEnvironmentValue:
    """Resolve modern -> deprecated legacy -> default without exposing values."""

    modern_present = name in environment
    legacy_present = legacy_name in environment
    if modern_present:
        modern_value = environment[name].strip()
        return ResolvedEnvironmentValue(
            name=name,
            legacy_name=legacy_name,
            value=modern_value,
            source=name,
            conflict=(
                legacy_present and modern_value != environment[legacy_name].strip()
            ),
        )
    if legacy_present:
        return ResolvedEnvironmentValue(
            name=name,
            legacy_name=legacy_name,
            value=environment[legacy_name].strip(),
            source=legacy_name,
            used_legacy=True,
        )
    return ResolvedEnvironmentValue(
        name=name,
        legacy_name=legacy_name,
        value=default,
        source="default",
    )


def load_relay_environment(
    environment: Mapping[str, str],
    *,
    base_default: str = STREAMHOUSE_RELAY_BASE_DEFAULT,
) -> RelayEnvironment:
    """Load every relay-specific setting through one precedence policy."""

    return RelayEnvironment(
        base=resolve_environment_value(
            environment,
            "STREAMHOUSE_RELAY_BASE",
            "SALLY_RELAY_BASE",
            default=base_default,
        ),
        keys=resolve_environment_value(
            environment,
            "STREAMHOUSE_RELAY_KEYS",
            "SALLY_RELAY_KEYS",
        ),
        database=resolve_environment_value(
            environment,
            "STREAMHOUSE_RELAY_DB",
            "SALLY_RELAY_DB",
        ),
    )
