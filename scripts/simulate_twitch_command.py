from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.routines import RoutineStore
from automation.simulator import TwitchCommandSimulator
from twitch.commands import TwitchCommandTriggerStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run a Twitch command routine without opening Sally."
    )
    parser.add_argument("message", help='Command text, for example "!sayhi bob"')
    parser.add_argument("--user", default="TestViewer", help="Viewer display name")
    parser.add_argument("--user-id", default="test-viewer", help="Viewer Twitch ID")
    parser.add_argument("--login", default="testviewer", help="Viewer login")
    parser.add_argument(
        "--broadcaster-id",
        default="broadcaster",
        help="Broadcaster Twitch ID",
    )
    parser.add_argument(
        "--badge",
        action="append",
        default=[],
        help="Viewer badge/role such as broadcaster, moderator, vip, subscriber",
    )
    parser.add_argument("--channel", default="", help="Broadcaster channel")
    parser.add_argument("--game", default="", help="Current Twitch category")
    parser.add_argument("--title", default="", help="Current stream title")
    parser.add_argument("--uptime", default="", help="Current stream uptime")
    parser.add_argument("--followers", default="", help="Follower count")
    parser.add_argument(
        "--muted",
        choices=("Muted", "Not Muted"),
        default="",
        help="OBS mute state for {mute}/{muted}",
    )
    parser.add_argument("--input", default="", help="OBS input/source name")
    args = parser.parse_args()

    routine_store = RoutineStore()
    command_store = TwitchCommandTriggerStore(routine_store=routine_store)
    command_store.load()
    context = {
        key: value
        for key, value in {
            "channel": args.channel,
            "game": args.game,
            "title": args.title,
            "uptime": args.uptime,
            "followers": args.followers,
            "muted": args.muted,
            "mute": args.muted,
            "input": args.input,
        }.items()
        if value
    }
    simulator = TwitchCommandSimulator(
        command_store,
        routine_store,
        live_context=context,
    )
    result = simulator.simulate(
        args.message,
        username=args.user,
        user_id=args.user_id,
        user_login=args.login,
        broadcaster_user_id=args.broadcaster_id,
        badges=tuple(args.badge),
    )

    print(f"Outcome: {result.outcome}")
    if result.invocation:
        print(f"Command: !{result.invocation}")
    if result.routine_name:
        print(f"Routine: {result.routine_name}")
    if result.remaining_seconds:
        print(f"Cooldown remaining: {result.remaining_seconds}s")
    if result.missing_variables:
        print("Missing variables: " + ", ".join(f"{{{v}}}" for v in result.missing_variables))
    if result.sent_messages:
        print("Chat output:")
        for sent in result.sent_messages:
            identity = "bot" if sent.as_bot else "broadcaster"
            print(f"  [{identity}] {sent.message}")
    if result.task_results:
        print("Tasks:")
        for task in result.task_results:
            status = "OK" if task.succeeded else "FAILED"
            print(f"  [{status}] {task.task_type}: {task.detail}")
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
