from core.logger import Logger
from app.app import run
import sys
from core.paths import migrate_legacy_user_data


def main():
    migrated_files = migrate_legacy_user_data()
    Logger.setup()

    # Log any unhandled crashes
    sys.excepthook = Logger.log_unhandled_exception

    Logger.info("Starting Sally AI...", source="APP")
    if migrated_files:
        Logger.info(
            "Migrated local data to Windows app storage: "
            + ", ".join(migrated_files),
            source="DATA",
        )

    run()

    Logger.info("Sally AI shut down.", source="APP")


if __name__ == "__main__":
    main()
