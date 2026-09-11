from shared.streamhouse_runtime.logger import Logger
from products.hub.streamhouse_hub.app import run
from products.hub.core.diagnostics import DiagnosticsService
from shared.streamhouse_runtime.paths import (
    smoke_test_enabled,
)


def main():
    smoke_test_enabled()
    diagnostics = DiagnosticsService()
    Logger.setup(
        session_id=diagnostics.session_id,
        product_name="StreamhouseHub",
        log_directory=diagnostics.logs_directory,
        retained_sessions=diagnostics.NORMAL_LOG_RETENTION,
    )
    diagnostics.install_exception_hooks()

    Logger.info("Starting Streamhouse Hub...", source="APP")

    run(diagnostics)


if __name__ == "__main__":
    main()
