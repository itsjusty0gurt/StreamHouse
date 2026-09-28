from __future__ import annotations

from PySide6.QtCore import QSettings


ORGANIZATION_NAME = "Streamhouse"
HUB_APPLICATION_NAME = "Streamhouse Hub"
AI_APPLICATION_NAME = "Streamhouse AI"


def streamhouse_qsettings(application_name: str) -> QSettings:
    return QSettings(ORGANIZATION_NAME, application_name)
