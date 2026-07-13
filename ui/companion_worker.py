from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from twitch.auth import TwitchToken
from twitch.live import TwitchHelixClient


@dataclass(frozen=True, slots=True)
class CompanionRefreshResult:
    request_id: int
    snapshot: dict
    chatters: tuple[dict, ...] = ()
    moderator_ids: frozenset[str] = frozenset()
    vip_ids: frozenset[str] = frozenset()
    subscriber_ids: frozenset[str] = frozenset()
    can_read_chatters: bool = False
    warnings: tuple[str, ...] = ()
    followers: tuple[dict, ...] = ()


class CompanionRefreshSignals(QObject):
    completed = Signal(object)
    failed = Signal(int, str)


class CompanionRefreshWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        helix: TwitchHelixClient,
        broadcaster_id: str,
        token: TwitchToken,
        fetch_followers: bool = False,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.helix = helix
        self.broadcaster_id = broadcaster_id
        self.token = token
        self.fetch_followers = fetch_followers
        self.signals = CompanionRefreshSignals()

    @Slot()
    def run(self) -> None:
        try:
            scopes = set(self.token.scopes)
            snapshot = self.helix.get_companion_snapshot(
                self.broadcaster_id,
                self.token,
            )
            can_read_chatters = "moderator:read:chatters" in scopes
            chatters: tuple[dict, ...] = ()
            moderator_ids: set[str] = set()
            vip_ids: set[str] = set()
            subscriber_ids: set[str] = set()
            warnings = [
                str(warning)
                for warning in snapshot.pop("warnings", [])
            ]
            followers: tuple[dict, ...] = ()
            if (
                self.fetch_followers
                and "moderator:read:followers" in scopes
            ):
                try:
                    followers = tuple(
                        self.helix.get_followers(
                            self.broadcaster_id,
                            self.token,
                        )
                    )
                except Exception as error:
                    warnings.append(f"follower history: {error}")
            if can_read_chatters:
                try:
                    chatters = tuple(
                        self.helix.get_chatters(
                            self.broadcaster_id,
                            self.token.user_id,
                            self.token,
                        )
                    )
                except Exception as error:
                    can_read_chatters = False
                    warnings.append(f"chatters: {error}")
                role_scopes = {
                    "moderation:read",
                    "moderator:read:vips",
                    "channel:read:subscriptions",
                }
                if can_read_chatters and role_scopes.issubset(scopes):
                    try:
                        moderator_ids, vip_ids, subscriber_ids = (
                            self.helix.get_chat_roles(
                                self.broadcaster_id,
                                self.token,
                            )
                        )
                    except Exception as error:
                        warnings.append(f"viewer roles: {error}")
            self.signals.completed.emit(
                CompanionRefreshResult(
                    request_id=self.request_id,
                    snapshot=snapshot,
                    chatters=chatters,
                    moderator_ids=frozenset(moderator_ids),
                    vip_ids=frozenset(vip_ids),
                    subscriber_ids=frozenset(subscriber_ids),
                    can_read_chatters=can_read_chatters,
                    warnings=tuple(warnings),
                    followers=followers,
                )
            )
        except Exception as error:
            self.signals.failed.emit(self.request_id, str(error))
