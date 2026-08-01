from __future__ import annotations

from PySide6.QtCore import QSettings


ORGANIZATION_NAME = "Streamhouse"
LEGACY_ORGANIZATION_NAME = "Sally AI"
HUB_APPLICATION_NAME = "Streamhouse Hub"
AI_APPLICATION_NAME = "Streamhouse AI"
LEGACY_HUB_APPLICATION_NAME = "Sally Bot"
LEGACY_AI_APPLICATION_NAME = "Sally AI Companion"


def migrate_qsettings_values(
    destination: QSettings,
    legacy: QSettings,
) -> int:
    """Copy missing legacy values without overwriting new Streamhouse state."""

    copied = 0
    destination_keys = set(destination.allKeys())
    for key in legacy.allKeys():
        if key in destination_keys:
            continue
        destination.setValue(key, legacy.value(key))
        destination_keys.add(key)
        copied += 1
    if copied:
        destination.sync()
    return copied


def streamhouse_qsettings(
    application_name: str,
    legacy_application_name: str,
) -> tuple[QSettings, int]:
    destination = QSettings(ORGANIZATION_NAME, application_name)
    legacy = QSettings(
        LEGACY_ORGANIZATION_NAME,
        legacy_application_name,
    )
    return destination, migrate_qsettings_values(destination, legacy)
