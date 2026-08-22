from shared.streamhouse_runtime.logger import Logger
from products.hub.streamhouse_hub.app import run
import sys
from shared.streamhouse_runtime.paths import (
    smoke_test_enabled,
)


def main():
    smoke_test_enabled()
    Logger.setup()

    # Log any unhandled crashes
    sys.excepthook = Logger.log_unhandled_exception

    Logger.info("Starting Streamhouse Hub...", source="APP")

    run()


if __name__ == "__main__":
    main()
