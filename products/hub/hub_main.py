from shared.streamhouse_runtime.logger import Logger
from products.hub.streamhouse_hub.app import run
import sys
from shared.streamhouse_runtime.paths import (
    consume_deprecation_warnings,
    migrate_legacy_user_data,
    smoke_test_enabled,
)


def main():
    smoke_test_enabled()
    migration = migrate_legacy_user_data()
    Logger.setup()

    # Log any unhandled crashes
    sys.excepthook = Logger.log_unhandled_exception

    for warning in consume_deprecation_warnings():
        Logger.warning(warning, source="CONFIG")
    Logger.info("Starting Streamhouse Hub...", source="APP")
    if migration.scanned_sources:
        Logger.info(
            "Legacy data migration checked "
            f"{len(migration.scanned_sources)} source(s): "
            f"{migration.copied_files} copied, "
            f"{migration.existing_files} already present, "
            f"{migration.failed_files} failed.",
            source="DATA",
        )

    run()


if __name__ == "__main__":
    main()
