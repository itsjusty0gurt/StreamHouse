from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.events import Events
from core.logger import Logger
from twitch.service import TwitchService
from twitch.auth import TwitchAuthService
from twitch.token_store import TwitchTokenStore
from config.twitch import TWITCH_BOT_SCOPES
from ui.main_window import MainWindow
from core.resources import resource_path
from config.version import VERSION


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


def run() -> None:
    """Create and run the Sally AI Bot desktop application."""

    Logger.timer_start("Application startup")

    Logger.info(
        "Creating Qt application.",
        source="UI",
    )

    application = QApplication(sys.argv)
    application.setApplicationName("Sally AI Bot")
    application.setOrganizationName("Sally AI")
    application.setApplicationVersion(VERSION)
    application.setWindowIcon(
        QIcon(str(resource_path("assets/sally-icon.png")))
    )

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
    window = MainWindow(
        twitch_service=twitch_service,
        twitch_auth=twitch_auth,
        twitch_bot_auth=twitch_bot_auth,
    )
    window.show()
    twitch_auth.restore()
    twitch_bot_auth.restore()

    if os.environ.get("SALLY_SMOKE_TEST") == "1":
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
