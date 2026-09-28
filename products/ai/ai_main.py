import sys

from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.paths import smoke_test_enabled
from products.ai.streamhouse_ai.app import run


def main() -> None:
    smoke_test_enabled()
    Logger.setup()
    sys.excepthook = Logger.log_unhandled_exception
    Logger.info("Starting Streamhouse AI...", source="AI")
    try:
        run()
    finally:
        Logger.info("Streamhouse AI shut down.", source="AI")
        Logger.shutdown()


if __name__ == "__main__":
    main()
