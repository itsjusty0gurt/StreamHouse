"""Forward the legacy Render entry point to the Streamhouse relay server."""

from extensions.twitch.app.relay_server import *  # noqa: F403
from extensions.twitch.app.relay_server import main


if __name__ == "__main__":
    main()
