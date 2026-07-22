import sys

from core.logger import Logger
from sally_companion.app import run


def main() -> None:
    Logger.setup()
    sys.excepthook = Logger.log_unhandled_exception
    Logger.info("Starting Sally AI Companion...", source="COMPANION")
    run()


if __name__ == "__main__":
    main()
