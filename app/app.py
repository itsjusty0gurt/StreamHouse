from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.events import Events
from core.logger import Logger
from ui.main_window import MainWindow


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

    register_events()

    Logger.info(
        "Creating main window.",
        source="UI",
    )

    window = MainWindow()
    window.show()

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

    sys.exit(exit_code)
