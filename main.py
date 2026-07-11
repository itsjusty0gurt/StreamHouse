from core.logger import Logger
from app.app import run
import sys


def main():
    Logger.setup()

    # Log any unhandled crashes
    sys.excepthook = Logger.log_unhandled_exception

    Logger.info("Starting Sally AI...", source="APP")

    run()

    Logger.info("Sally AI shut down.", source="APP")


if __name__ == "__main__":
    main()