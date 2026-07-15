from __future__ import annotations

import json
import threading
import time
import webbrowser
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config.twitch import TWITCH_CLIENT_ID, TWITCH_SCOPES
from core.events import Events
from core.logger import Logger
from twitch.token_store import TwitchTokenStore


class TwitchAuthState(StrEnum):
    SIGNED_OUT = "Signed out"
    WAITING = "Waiting for authorization"
    SIGNED_IN = "Signed in"
    ERROR = "Authentication error"


@dataclass(slots=True)
class TwitchToken:
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: list[str]
    user_id: str = ""
    login: str = ""

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> TwitchToken:
        return cls(
            access_token=str(values["access_token"]),
            refresh_token=str(values["refresh_token"]),
            expires_at=float(values["expires_at"]),
            scopes=[str(scope) for scope in values.get("scopes", [])],
            user_id=str(values.get("user_id", "")),
            login=str(values.get("login", "")),
        )


class TwitchAuthClient:
    DEVICE_URL = "https://id.twitch.tv/oauth2/device"
    TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"

    @staticmethod
    def _request_json(request: Request, timeout: float = 15) -> dict[str, Any]:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Twitch returned an invalid response.")
        return payload

    def start_device_flow(
        self,
        scopes: tuple[str, ...] = TWITCH_SCOPES,
    ) -> dict[str, Any]:
        body = urlencode(
            {"client_id": TWITCH_CLIENT_ID, "scopes": " ".join(scopes)}
        ).encode("ascii")
        return self._request_json(Request(self.DEVICE_URL, data=body, method="POST"))

    def exchange_device_code(self, device_code: str, scopes: list[str]) -> TwitchToken:
        body = urlencode(
            {
                "client_id": TWITCH_CLIENT_ID,
                "scopes": " ".join(scopes),
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
        ).encode("ascii")
        values = self._request_json(Request(self.TOKEN_URL, data=body, method="POST"))
        return TwitchToken(
            access_token=str(values["access_token"]),
            refresh_token=str(values["refresh_token"]),
            expires_at=time.time() + int(values["expires_in"]),
            scopes=[str(scope) for scope in values.get("scope", scopes)],
        )

    def validate(self, token: TwitchToken) -> TwitchToken:
        request = Request(
            self.VALIDATE_URL,
            headers={"Authorization": f"OAuth {token.access_token}"},
        )
        values = self._request_json(request)
        if values.get("client_id") != TWITCH_CLIENT_ID:
            raise ValueError("The saved token belongs to another Twitch application.")
        token.user_id = str(values.get("user_id", ""))
        token.login = str(values.get("login", ""))
        token.scopes = [str(scope) for scope in values.get("scopes", token.scopes)]
        token.expires_at = time.time() + int(values.get("expires_in", 0))
        return token

    def refresh(self, token: TwitchToken) -> TwitchToken:
        body = urlencode(
            {
                "client_id": TWITCH_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            }
        ).encode("ascii")
        values = self._request_json(Request(self.TOKEN_URL, data=body, method="POST"))
        return TwitchToken(
            access_token=str(values["access_token"]),
            refresh_token=str(values["refresh_token"]),
            expires_at=time.time() + int(values["expires_in"]),
            scopes=[str(scope) for scope in values.get("scope", token.scopes)],
            user_id=token.user_id,
            login=token.login,
        )


class TwitchAuthService:
    """Run Twitch's Public-client Device Code flow outside the UI thread."""

    def __init__(
        self,
        client: TwitchAuthClient | None = None,
        store: TwitchTokenStore | None = None,
        scopes: tuple[str, ...] = TWITCH_SCOPES,
        event_name: str = "twitch_auth_changed",
        account_label: str = "Twitch",
    ) -> None:
        self.client = client or TwitchAuthClient()
        self.store = store or TwitchTokenStore()
        self.scopes = tuple(scopes)
        self.event_name = event_name
        self.account_label = account_label
        self.state = TwitchAuthState.SIGNED_OUT
        self.token: TwitchToken | None = None
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_unauthorized_recovery = 0.0

    def restore(self) -> None:
        try:
            token = self.store.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            Logger.warning(f"Could not load Twitch credentials: {error}", source="TWITCH")
            self.store.clear()
            self._set_state(
                TwitchAuthState.SIGNED_OUT,
                "Saved Twitch sign-in could not be restored",
            )
            return
        if token is None:
            return
        self._run_async(self._validate_saved, token)

    def maintain(self) -> None:
        if self.token is None or (self._worker and self._worker.is_alive()):
            return
        self._run_async(self._maintain_token, self.token)

    def missing_scopes(self, scopes: set[str] | frozenset[str]) -> set[str]:
        granted = set(self.token.scopes) if self.token is not None else set()
        return set(scopes) - granted

    def sign_in(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._cancel.clear()
        self._run_async(self._device_flow)

    def recover_unauthorized(self) -> bool:
        """Refresh once after an API 401, with a cooldown against loops."""
        now = time.monotonic()
        if (
            self.token is None
            or not self.token.refresh_token
            or (self._worker and self._worker.is_alive())
            or now - self._last_unauthorized_recovery < 300
        ):
            return False
        self._last_unauthorized_recovery = now
        self._run_async(self._refresh_after_unauthorized, self.token)
        return True

    def sign_out(self) -> None:
        self._cancel.set()
        self.token = None
        self.store.clear()
        self._set_state(TwitchAuthState.SIGNED_OUT, "Not signed in")

    def _run_async(self, function: Any, *args: Any) -> None:
        self._worker = threading.Thread(target=function, args=args, daemon=True)
        self._worker.start()

    def _validate_saved(self, token: TwitchToken) -> None:
        try:
            try:
                self.token = self.client.validate(token)
            except HTTPError as error:
                if error.code != 401 or not token.refresh_token:
                    raise
                self.token = self.client.validate(self.client.refresh(token))
            missing_scopes = set(self.scopes) - set(self.token.scopes)
            if missing_scopes:
                Logger.info(
                    "Saved Twitch login is missing newer optional permissions: "
                    + ", ".join(sorted(missing_scopes)),
                    source="TWITCH",
                )
            self.store.save(self.token)
            self._set_state(TwitchAuthState.SIGNED_IN, self.token.login)
        except (HTTPError, URLError, OSError, ValueError) as error:
            self.store.clear()
            self._set_state(TwitchAuthState.SIGNED_OUT, "Not signed in")
            Logger.warning(f"Saved Twitch sign-in expired: {error}", source="TWITCH")

    def _maintain_token(self, token: TwitchToken) -> None:
        try:
            if token.expires_at <= time.time() + 900:
                token = self.client.refresh(token)
            self.token = self.client.validate(token)
            self.store.save(self.token)
            self._set_state(TwitchAuthState.SIGNED_IN, self.token.login)
        except (HTTPError, URLError, OSError, ValueError) as error:
            self.token = None
            self.store.clear()
            self._set_state(TwitchAuthState.ERROR, f"Twitch session expired: {error}")

    def _refresh_after_unauthorized(self, token: TwitchToken) -> None:
        try:
            refreshed = self.client.refresh(token)
            self.token = self.client.validate(refreshed)
            self.store.save(self.token)
            Logger.info(
                "Refreshed Twitch login after an unauthorized API response.",
                source="TWITCH",
            )
            self._set_state(TwitchAuthState.SIGNED_IN, self.token.login)
        except (HTTPError, URLError, OSError, ValueError) as error:
            self.token = None
            self.store.clear()
            self._set_state(
                TwitchAuthState.ERROR,
                f"Twitch session recovery failed: {error}",
            )

    def _device_flow(self) -> None:
        try:
            values = self.client.start_device_flow(self.scopes)
            code = str(values["user_code"])
            verification_url = str(values["verification_uri"])
            interval = max(int(values.get("interval", 5)), 1)
            deadline = time.monotonic() + int(values["expires_in"])
            self._set_state(
                TwitchAuthState.WAITING,
                f"Enter {code} at twitch.tv/activate",
            )
            webbrowser.open(verification_url)
            while not self._cancel.wait(interval) and time.monotonic() < deadline:
                try:
                    token = self.client.exchange_device_code(
                        str(values["device_code"]), list(self.scopes)
                    )
                except HTTPError as error:
                    if error.code == 400:
                        continue
                    raise
                if self._cancel.is_set():
                    return
                self.token = self.client.validate(token)
                self.store.save(self.token)
                self._set_state(TwitchAuthState.SIGNED_IN, self.token.login)
                return
            if not self._cancel.is_set():
                raise TimeoutError("The Twitch activation code expired.")
        except (HTTPError, URLError, OSError, ValueError, KeyError, TimeoutError) as error:
            self._set_state(TwitchAuthState.ERROR, str(error))

    def _set_state(self, state: TwitchAuthState, detail: str) -> None:
        self.state = state
        Events.emit(self.event_name, state=state, detail=detail)
