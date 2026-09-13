from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from products.hub.core.diagnostics import DiagnosticsService
from products.hub.core.single_instance import HubInstanceLock, InstanceLockError
from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.paths import smoke_test_enabled, user_data_root


def _show_startup_notice(message: str) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("Streamhouse Hub")
    QMessageBox.information(None, "Streamhouse Hub", message)


def main(
    *,
    data_root: Path | None = None,
    lock_factory: Callable[[Path], HubInstanceLock] = HubInstanceLock,
    diagnostics_factory: Callable[[Path], DiagnosticsService] = DiagnosticsService,
    runner: Callable[[DiagnosticsService], None] | None = None,
    notice: Callable[[str], None] = _show_startup_notice,
) -> int:
    smoke_test_enabled()
    resolved_root = (data_root or user_data_root()).expanduser().resolve()
    ownership = lock_factory(resolved_root)
    try:
        acquired = ownership.try_acquire()
    except InstanceLockError:
        notice(
            "Streamhouse Hub could not secure exclusive access to this setup "
            "and will not start."
        )
        return 1
    if not acquired:
        notice(
            "Streamhouse Hub is already running.\n\n"
            "Only one Hub instance can use this setup at a time."
        )
        return 0

    try:
        diagnostics = diagnostics_factory(resolved_root)
        Logger.setup(
            session_id=diagnostics.session_id,
            product_name="StreamhouseHub",
            log_directory=diagnostics.logs_directory,
            retained_sessions=diagnostics.NORMAL_LOG_RETENTION,
        )
        diagnostics.install_exception_hooks()

        Logger.info("Starting Streamhouse Hub...", source="APP")

        if runner is None:
            from products.hub.streamhouse_hub.app import run

            runner = run
        runner(diagnostics)
        return 0
    finally:
        # app.run() completes writable teardown, diagnostics, and logging first.
        # SystemExit still unwinds through this finally block.
        ownership.release()


if __name__ == "__main__":
    sys.exit(main())
