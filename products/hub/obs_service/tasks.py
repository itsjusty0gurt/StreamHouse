from __future__ import annotations

import json

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.variables import render_preview
from products.hub.obs_service.service import ObsWebSocketService


OBS_TASK_LABELS = {
    "obs.set_program_scene": "OBS — Change scene",
    "obs.set_preview_scene": "OBS — Switch preview scene",
    "obs.set_scene_item_enabled": "OBS — Show, hide, or toggle source",
    "obs.set_input_mute": "OBS — Mute, unmute, or toggle input",
    "obs.set_input_volume": "OBS — Set input volume",
    "obs.set_source_filter_state": "OBS - Enable, disable, or toggle source filter",
    "obs.set_scene_filter_state": "OBS - Enable, disable, or toggle scene filter",
    "obs.set_text_source": "OBS — Set text source",
    "obs.set_image_source": "OBS — Set image source",
    "obs.stream_control": "OBS — Start or stop streaming",
    "obs.record_control": "OBS — Control recording",
    "obs.replay_buffer_control": "OBS — Control replay buffer",
    "obs.media_control": "OBS — Control media source",
    "obs.trigger_hotkey": "OBS — Trigger hotkey",
    "obs.set_studio_mode": "OBS — Set Studio Mode",
    "obs.raw_request": "OBS — Advanced request",
}


class ObsTask:
    def __init__(self, service: ObsWebSocketService, task_type: str) -> None:
        self.service = service
        self.task_type = task_type

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        try:
            if self.task_type == "obs.set_scene_item_enabled":
                c = task.config
                request_id = self.service.set_scene_item_enabled(
                    self._required(c, "scene"),
                    self._required(c, "source"),
                    str(c.get("action", "show")).casefold(),
                )
                return TaskExecutionResult(
                    task.task_id,
                    task.task_type,
                    bool(request_id),
                    "Queued OBS source visibility request."
                    if request_id else "OBS is not connected.",
                )
            if self.task_type in {
                "obs.set_source_filter_state",
                "obs.set_scene_filter_state",
            }:
                c = task.config
                source = (
                    self._required(c, "scene")
                    if self.task_type == "obs.set_scene_filter_state"
                    else self._required(c, "source")
                )
                request_id = self.service.set_source_filter_enabled(
                    source,
                    self._required(c, "filter"),
                    str(c.get("action", "toggle")).casefold(),
                )
                return TaskExecutionResult(
                    task.task_id,
                    task.task_type,
                    bool(request_id),
                    "Queued OBS filter state request."
                    if request_id else "OBS is not connected.",
                )
            request_type, request_data = self._request(task, trigger)
            request_id = self.service.send_request(request_type, request_data)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return TaskExecutionResult(task.task_id, task.task_type, False, str(error))
        return TaskExecutionResult(
            task.task_id,
            task.task_type,
            bool(request_id),
            f"Queued OBS request {request_type}." if request_id else "OBS is not connected.",
        )

    def _request(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> tuple[str, dict[str, object]]:
        c = task.config
        if self.task_type == "obs.set_program_scene":
            return "SetCurrentProgramScene", {"sceneName": self._required(c, "scene")}
        if self.task_type == "obs.set_preview_scene":
            return "SetCurrentPreviewScene", {"sceneName": self._required(c, "scene")}
        if self.task_type == "obs.set_input_mute":
            name, action = self._required(c, "input"), str(c.get("action", "toggle")).casefold()
            if action == "toggle":
                return "ToggleInputMute", {"inputName": name}
            return "SetInputMute", {"inputName": name, "inputMuted": action == "mute"}
        if self.task_type == "obs.set_input_volume":
            return "SetInputVolume", {"inputName": self._required(c, "input"), "inputVolumeDb": float(c.get("volume_db", 0))}
        if self.task_type == "obs.set_text_source":
            text = render_preview(str(c.get("text", "")), trigger.context)
            return "SetInputSettings", {
                "inputName": self._required(c, "input"),
                "inputSettings": {"text": text},
                "overlay": True,
            }
        if self.task_type == "obs.set_image_source":
            image_file = render_preview(
                self._required(c, "file"),
                trigger.context,
            ).strip()
            if not image_file:
                raise ValueError("OBS task requires an image file.")
            return "SetInputSettings", {
                "inputName": self._required(c, "input"),
                "inputSettings": {"file": image_file},
                "overlay": True,
            }
        if self.task_type == "obs.stream_control":
            return ("StartStream" if c.get("action", "start") == "start" else "StopStream"), {}
        if self.task_type == "obs.record_control":
            return {"start": "StartRecord", "stop": "StopRecord", "pause": "PauseRecord", "resume": "ResumeRecord"}.get(str(c.get("action")), "StartRecord"), {}
        if self.task_type == "obs.replay_buffer_control":
            return {"start": "StartReplayBuffer", "stop": "StopReplayBuffer", "save": "SaveReplayBuffer"}.get(str(c.get("action")), "StartReplayBuffer"), {}
        if self.task_type == "obs.media_control":
            action = str(c.get("action", "play")).upper()
            return "TriggerMediaInputAction", {"inputName": self._required(c, "input"), "mediaAction": f"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_{action}"}
        if self.task_type == "obs.trigger_hotkey":
            return "TriggerHotkeyByName", {"hotkeyName": self._required(c, "hotkey")}
        if self.task_type == "obs.set_studio_mode":
            return "SetStudioModeEnabled", {"studioModeEnabled": bool(c.get("enabled", True))}
        if self.task_type == "obs.raw_request":
            raw = c.get("request_data", {})
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                raise ValueError("OBS request data must be a JSON object.")
            return self._required(c, "request_type"), data
        raise ValueError(f"Unsupported OBS task type: {self.task_type}")

    @staticmethod
    def _required(config: dict, key: str) -> str:
        value = str(config.get(key, "")).strip()
        if not value:
            raise ValueError(f"OBS task requires {key.replace('_', ' ')}.")
        return value


def register_obs_tasks(registry, service: ObsWebSocketService) -> None:
    for task_type in OBS_TASK_LABELS:
        registry.register(ObsTask(service, task_type))
