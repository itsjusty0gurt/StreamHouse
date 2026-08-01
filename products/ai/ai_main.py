import sys

from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.paths import (
    consume_deprecation_warnings,
    migrate_legacy_user_data,
    smoke_test_enabled,
)
from products.ai.streamhouse_ai.app import run


def main() -> None:
    smoke_test_enabled()
    migration = migrate_legacy_user_data()
    Logger.setup()
    sys.excepthook = Logger.log_unhandled_exception
    for warning in consume_deprecation_warnings():
        Logger.warning(warning, source="CONFIG")
    Logger.info("Starting Streamhouse AI...", source="AI")
    if migration.scanned_sources:
        Logger.info(
            "Legacy data migration checked "
            f"{len(migration.scanned_sources)} source(s): "
            f"{migration.copied_files} copied, "
            f"{migration.existing_files} already present, "
            f"{migration.failed_files} failed.",
            source="DATA",
        )
    try:
        run()
    finally:
        Logger.info("Streamhouse AI shut down.", source="AI")
        Logger.shutdown()


if __name__ == "__main__":
    main()
