from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from products.hub.core.events import Events
from shared.streamhouse_runtime.logger import Logger
from products.hub.twitch.service import TwitchService
from products.hub.twitch.auth import TwitchAuthService
from products.hub.twitch.token_store import TwitchTokenStore
from products.hub.config.twitch import TWITCH_BOT_SCOPES
from products.hub.ui.main_window import MainWindow
from products.hub.core.resources import resource_path
from shared.streamhouse_runtime.paths import smoke_test_enabled
from shared.streamhouse_runtime.qt_settings import (
    HUB_APPLICATION_NAME,
    LEGACY_HUB_APPLICATION_NAME,
    ORGANIZATION_NAME,
    streamhouse_qsettings,
)
from products.hub.core.window_state import WindowStateStore
from shared.streamhouse_runtime.version import VERSION


def register_events() -> None:
    """
    Register application-wide event listeners.

    This will later connect the UI, Twitch, AI, voice, memory,
    OBS, and plugin systems.
    """

    Logger.info(
        "Registering application events.",
        source="APP",
    )

    Logger.info(
        (
            f"Registered {Events.listener_count()} "
            "application event listener(s)."
        ),
        source="APP",
    )


def shutdown_application() -> None:
    """Perform application cleanup before exiting."""

    Logger.info(
        "Application shutdown beginning.",
        source="APP",
    )

    removed_listeners = Events.clear()

    Logger.info(
        (
            f"Removed {removed_listeners} "
            "application event listener(s)."
        ),
        source="APP",
    )

    Logger.info(
        "Application cleanup complete.",
        source="APP",
    )


def configure_application(application: QApplication) -> None:
    application.setApplicationName(HUB_APPLICATION_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    application.setApplicationVersion(VERSION)
    application.setWindowIcon(
        QIcon(str(resource_path("assets/sally-icon.png")))
    )


def run() -> None:
    """Create and run the Streamhouse Hub desktop application."""

    Logger.timer_start("Application startup")

    Logger.info(
        "Creating Qt application.",
        source="UI",
    )

    application = QApplication(sys.argv)
    configure_application(application)

    register_events()

    Logger.info(
        "Creating main window.",
        source="UI",
    )

    twitch_auth = TwitchAuthService()
    twitch_bot_auth = TwitchAuthService(
        store=TwitchTokenStore.bot_account(),
        scopes=TWITCH_BOT_SCOPES,
        event_name="twitch_bot_auth_changed",
        account_label="Sally bot",
    )
    twitch_service = TwitchService(
        auth=twitch_auth,
        bot_auth=twitch_bot_auth,
    )
    window_settings, migrated_qt_values = streamhouse_qsettings(
        HUB_APPLICATION_NAME,
        LEGACY_HUB_APPLICATION_NAME,
    )
    if migrated_qt_values:
        Logger.info(
            f"Migrated {migrated_qt_values} Hub UI preference(s).",
            source="DATA",
        )
    window = MainWindow(
        twitch_service=twitch_service,
        twitch_auth=twitch_auth,
        twitch_bot_auth=twitch_bot_auth,
        window_state_store=WindowStateStore(window_settings),
    )
    window.show()
    twitch_auth.restore()
    twitch_bot_auth.restore()
    QTimer.singleShot(0, window.fire_application_started_trigger)
    QTimer.singleShot(250, window.auto_connect_obs)
    QTimer.singleShot(350, window.auto_connect_soundboard_relay)

    if smoke_test_enabled():
        QTimer.singleShot(750, window.close)
        QTimer.singleShot(900, application.quit)

    Logger.info(
        "Main window displayed.",
        source="UI",
    )

    Logger.timer_end(
        "Application startup",
        source="APP",
    )

    Logger.info(
        "Qt event loop started.",
        source="UI",
    )

    exit_code = application.exec()

    Logger.info(
        f"Qt event loop ended with exit code {exit_code}.",
        source="UI",
    )

    shutdown_application()
    Logger.info("Streamhouse Hub shut down.", source="APP")

    if exit_code != 0:
        Logger.warning(
            (
                "Application exited with a "
                f"non-zero exit code: {exit_code}."
            ),
            source="APP",
        )

    Logger.shutdown()
    sys.exit(exit_code)
