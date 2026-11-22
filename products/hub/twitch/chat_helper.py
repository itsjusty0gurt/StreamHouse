"""Local Twitch chat suggestions and broadcaster mention matching."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from heapq import nlargest

from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.twitch.models import TwitchMessage


@dataclass(frozen=True, slots=True)
class TwitchUserSuggestion:
    login: str
    display_name: str = ""
    user_id: str = ""
    last_seen: str = ""


@dataclass(frozen=True, slots=True)
class ChatCommandSuggestion:
    name: str
    source: str
    description: str = ""


def matching_chat_commands(
    store: TwitchCommandTriggerStore, query: str, *, limit: int = 20
) -> tuple[ChatCommandSuggestion, ...]:
    commands = {}
    for command in store.triggers:
        if not command.enabled:
            continue
        source = "Default" if command.is_default else "Custom"
        for name in (command.name, *command.aliases):
            if name.startswith(query):
                description = f"Alias for !{command.name}" if name != command.name else ""
                commands.setdefault(name, ChatCommandSuggestion(name, source, description))
    external_names = store.external_listing_names()
    for command in store.external_commands:
        if command.name in external_names and command.enabled and command.name.startswith(query):
            commands.setdefault(command.name, ChatCommandSuggestion(
                command.name, "External", command.description
            ))
    return tuple(commands[name] for name in sorted(commands)[:limit])


def matching_chat_users(
    users: Iterable[TwitchUserSuggestion], query: str, *, limit: int = 20
) -> tuple[TwitchUserSuggestion, ...]:
    seen_ids: set[str] = set()
    seen_logins: set[str] = set()

    def matches():
        for user in users:
            login = user.login.strip().lstrip("@").casefold()
            if not re.fullmatch(r"[a-z0-9_]+", login):
                continue
            if login in seen_logins or (user.user_id and user.user_id in seen_ids):
                continue
            if query not in login and query not in user.display_name.casefold():
                continue
            seen_logins.add(login)
            if user.user_id:
                seen_ids.add(user.user_id)
            yield TwitchUserSuggestion(login, user.display_name, user.user_id, user.last_seen)

    return tuple(nlargest(
        limit, matches(), key=lambda user: (user.login.startswith(query), user.last_seen)
    ))


_MENTION = re.compile(r"(?<![\w@/])@([\w]+)")


def mentions_broadcaster(text: str, login: str, display_name: str) -> bool:
    identities = {login.strip().casefold(), display_name.strip().casefold()} - {""}
    return bool(identities) and any(
        match[1].casefold() in identities for match in _MENTION.finditer(text)
    )


def mentions_authenticated_broadcaster(
    message: TwitchMessage,
    token: TwitchToken | None,
    connected_user_id: str,
    connected_display_name: str,
) -> bool:
    if token is None or not token.user_id or message.broadcaster_user_id not in ("", token.user_id):
        return False
    display_name = (
        message.broadcaster_user_name if message.broadcaster_user_id == token.user_id else ""
    )
    if not display_name and connected_user_id == token.user_id:
        display_name = connected_display_name
    return mentions_broadcaster(message.text, token.login, display_name)
