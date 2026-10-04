from __future__ import annotations

import json
from dataclasses import dataclass
from secrets import token_hex
from typing import Mapping


SCRIPT_CONTEXT_ENV = "STREAMHOUSE_SCRIPT_VARIABLES"
SCRIPT_TOKEN_ENV = "STREAMHOUSE_SCRIPT_TOKEN"
SCRIPT_PROTOCOL_PREFIX = "__STREAMHOUSE_SCRIPT_CONTEXT__"


SCRIPT_BOOTSTRAP = r'''
import json
import os
import re
import runpy
import sys

_OUTPUT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VARIABLE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*\.[a-z0-9_.-]+$")


def _display_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


class _HubContext:
    def __init__(self, values):
        self._active = True
        self._values = dict(values)
        self._outputs = {}
        self._logs = []

    def _require_active(self):
        if not self._active:
            raise RuntimeError("Script execution context is no longer active")

    def set_output(self, name, value):
        self._require_active()
        clean = str(name).strip().casefold()
        if clean.startswith("automation.") or not _OUTPUT_PATTERN.fullmatch(clean):
            raise ValueError(f"Invalid automation output name: {name}")
        rendered = _display_value(value)
        self._outputs[clean] = rendered
        self._values[f"automation.{clean}"] = rendered

    def get_variable(self, name):
        self._require_active()
        clean = str(name).strip().casefold()
        if clean.startswith("{") and clean.endswith("}"):
            clean = clean[1:-1].strip()
        if not _VARIABLE_PATTERN.fullmatch(clean) or clean not in self._values:
            raise LookupError(
                "Variable is not available in this execution context: " + clean
            )
        return self._values[clean]

    def log(self, message):
        self._require_active()
        self._logs.append(str(message)[:4000])

    def _close(self):
        self._active = False


_values = json.loads(os.environ.get("STREAMHOUSE_SCRIPT_VARIABLES", "{}"))
hub = _HubContext(_values if isinstance(_values, dict) else {})
_script = sys.argv[1]
sys.argv = [_script, *sys.argv[2:]]
_script_directory = os.path.dirname(os.path.abspath(_script))
if _script_directory not in sys.path:
    sys.path.insert(0, _script_directory)
try:
    runpy.run_path(_script, run_name="__main__", init_globals={"hub": hub})
finally:
    hub._close()
    _payload = json.dumps(
        {"outputs": hub._outputs, "logs": hub._logs},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stderr.write(
        "__STREAMHOUSE_SCRIPT_CONTEXT__"
        + os.environ.get("STREAMHOUSE_SCRIPT_TOKEN", "")
        + ":"
        + _payload
        + "\n"
    )
    sys.stderr.flush()
'''


@dataclass(frozen=True, slots=True)
class ScriptBridgePayload:
    outputs: Mapping[str, str]
    logs: tuple[str, ...]


def script_bridge_environment(
    values: Mapping[str, object],
) -> tuple[str, dict[str, str]]:
    token = token_hex(16)
    encoded_values = {
        str(name): _display_value(value) for name, value in values.items()
    }
    return token, {
        SCRIPT_CONTEXT_ENV: json.dumps(
            encoded_values,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        SCRIPT_TOKEN_ENV: token,
    }


def script_bridge_command(
    interpreter: list[str],
    script: str,
    arguments: list[str],
) -> list[str]:
    return [*interpreter, "-c", SCRIPT_BOOTSTRAP, script, *arguments]


def parse_script_bridge(
    stderr: str,
    token: str,
) -> tuple[ScriptBridgePayload | None, str]:
    marker = f"{SCRIPT_PROTOCOL_PREFIX}{token}:"
    payload: ScriptBridgePayload | None = None
    ordinary_lines: list[str] = []
    for line in stderr.splitlines():
        if not line.startswith(marker):
            ordinary_lines.append(line)
            continue
        if payload is not None:
            raise ValueError("Python script returned duplicate context results.")
        try:
            decoded = json.loads(line[len(marker) :])
        except json.JSONDecodeError as error:
            raise ValueError("Python script returned invalid context results.") from error
        if not isinstance(decoded, dict):
            raise ValueError("Python script returned invalid context results.")
        raw_outputs = decoded.get("outputs", {})
        raw_logs = decoded.get("logs", [])
        if not isinstance(raw_outputs, dict) or not isinstance(raw_logs, list):
            raise ValueError("Python script returned invalid context results.")
        payload = ScriptBridgePayload(
            outputs={
                str(name): _display_value(value)
                for name, value in raw_outputs.items()
            },
            logs=tuple(str(message) for message in raw_logs),
        )
    return payload, "\n".join(ordinary_lines).strip()


def _display_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)
