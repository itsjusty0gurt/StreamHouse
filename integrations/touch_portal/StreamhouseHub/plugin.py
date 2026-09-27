from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLUGIN_ID = "com.streamhouse.hub"
RUN_ROUTINE_ACTION_ID = f"{PLUGIN_ID}.run_routine"
ROUTINE_CHOICE_ID = f"{PLUGIN_ID}.routine"
TOUCH_PORTAL_HOST = "127.0.0.1"
TOUCH_PORTAL_PORT = 12136
HUB_API_URL = "http://127.0.0.1:8766/streamhouse/integration/v1"
PROTOCOL_VERSION = 1
HUB_UNAVAILABLE_CHOICE = "Hub unavailable"
REFRESH_SECONDS = 10.0


class HubApiError(RuntimeError):
    pass


class PluginClosed(RuntimeError):
    pass


class HubApiClient:
    def __init__(self, url: str = HUB_API_URL, timeout: float = 3.0) -> None:
        self.url = url
        self.timeout = timeout

    def request(self, request_type: str, **values: Any) -> dict[str, Any]:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "type": request_type,
            **values,
        }
        request = Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                result = json.loads(error.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HubApiError("Hub rejected the request.") from None
        except (OSError, URLError) as error:
            raise HubApiError("Streamhouse Hub is unavailable.") from error
        if not isinstance(result, dict):
            raise HubApiError("Hub returned an invalid response.")
        if result.get("success") is False:
            detail = result.get("error", {})
            message = detail.get("message") if isinstance(detail, dict) else ""
            raise HubApiError(str(message or "Hub rejected the request."))
        return result

    def list_routines(self) -> list[dict[str, str]]:
        response = self.request("list_routines")
        values = response.get("routines", [])
        if not isinstance(values, list):
            raise HubApiError("Hub returned an invalid routine list.")
        return [
            {
                "id": str(item.get("id", "")),
                "name": str(item.get("name", "")),
                "group": str(item.get("group", "")),
            }
            for item in values
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]

    def run_routine(self, routine_id: str) -> None:
        self.request("run_routine", routine_id=routine_id)


def routine_choice(routine: dict[str, str]) -> str:
    name = routine.get("name", "").strip() or "Unnamed Routine"
    group = routine.get("group", "").strip()
    label = f"{group} / {name}" if group else name
    return f"{label}  [{routine['id']}]"


def routine_id_from_choice(value: str) -> str:
    value = value.strip()
    opening = value.rfind("  [")
    if opening < 0 or not value.endswith("]"):
        raise ValueError("Select a current Streamhouse Hub routine.")
    routine_id = value[opening + 3 : -1].strip()
    if not routine_id:
        raise ValueError("Select a current Streamhouse Hub routine.")
    return routine_id


class TouchPortalPlugin:
    def __init__(self, hub: HubApiClient | None = None) -> None:
        self.hub = hub or HubApiClient()
        self._socket: socket.socket | None = None
        self._writer = None
        self._last_choices: tuple[str, ...] = ()

    def run_forever(self) -> None:
        delay = 2.0
        while True:
            try:
                self._run_connection()
                delay = 2.0
            except PluginClosed:
                self.close()
                return
            except (OSError, EOFError, json.JSONDecodeError) as error:
                logging.warning("Touch Portal connection lost: %s", error)
            self.close()
            time.sleep(delay)
            delay = min(delay * 1.5, 10.0)

    def _run_connection(self) -> None:
        connection = socket.create_connection(
            (TOUCH_PORTAL_HOST, TOUCH_PORTAL_PORT),
            timeout=5.0,
        )
        connection.settimeout(1.0)
        self._socket = connection
        self._writer = connection.makefile("w", encoding="utf-8", newline="\n")
        self._send({"type": "pair", "id": PLUGIN_ID})
        next_refresh: float | None = None
        buffer = b""
        while True:
            now = time.monotonic()
            if next_refresh is not None and now >= next_refresh:
                self.refresh_routines()
                next_refresh = now + REFRESH_SECONDS
            try:
                chunk = connection.recv(16 * 1024)
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError("Touch Portal closed the plugin connection.")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line:
                    continue
                message = json.loads(line.decode("utf-8"))
                if isinstance(message, dict):
                    self.handle_message(message)
                    if message.get("type") == "info":
                        next_refresh = time.monotonic() + REFRESH_SECONDS

    def handle_message(self, message: dict[str, Any]) -> None:
        if message.get("type") == "closePlugin":
            raise PluginClosed("Touch Portal requested plugin shutdown.")
        if message.get("type") == "info":
            self.refresh_routines()
            return
        if (
            message.get("type") != "action"
            or message.get("actionId") != RUN_ROUTINE_ACTION_ID
        ):
            return
        values = message.get("data", [])
        selected = next(
            (
                str(item.get("value", ""))
                for item in values
                if isinstance(item, dict) and item.get("id") == ROUTINE_CHOICE_ID
            ),
            "",
        )
        try:
            self.hub.run_routine(routine_id_from_choice(selected))
        except (HubApiError, ValueError) as error:
            logging.error("Run Routine failed: %s", error)

    def refresh_routines(self) -> None:
        try:
            choices = tuple(routine_choice(item) for item in self.hub.list_routines())
        except HubApiError:
            choices = (HUB_UNAVAILABLE_CHOICE,)
        if not choices:
            choices = ("No enabled Hub routines",)
        if choices == self._last_choices:
            return
        self._last_choices = choices
        self._send(
            {
                "type": "choiceUpdate",
                "id": ROUTINE_CHOICE_ID,
                "value": list(choices),
            }
        )

    def _send(self, payload: dict[str, Any]) -> None:
        if self._writer is None:
            return
        self._writer.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self._writer.flush()

    def close(self) -> None:
        for stream in (self._writer,):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._writer = None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    TouchPortalPlugin().run_forever()


if __name__ == "__main__":
    main()
