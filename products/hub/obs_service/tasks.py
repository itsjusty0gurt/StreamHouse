from __future__ import annotations

import json
import math

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.variable_registry import render_placeholders
from products.hub.automation.variable_outputs import automation_output_name
from products.hub.obs_service.models import ObsRequestResult
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
    "obs.set_browser_source_url": "OBS — Set Browser Source URL",
    "obs.set_media_source_file": "OBS — Set Media Source File",
    "obs.set_source_audio_track": "OBS — Set Source Audio Track",
    "obs.set_color_source_color": "OBS — Set Color Source Color",
    "obs.restart_media_source": "OBS — Restart Media Source",
    "obs.set_transition": "OBS — Set Transition",
    "obs.take_screenshot": "OBS — Take Screenshot",
    "obs.set_source_transform": "OBS — Set Source Transform",
    "obs.create_record_chapter": "OBS — Create Record Chapter",
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
                result = self.service.set_scene_item_enabled(
                    self._required(c, "scene"),
                    self._required(c, "source"),
                    str(c.get("action", "show")).casefold(),
                )
                return self._result(task, result)
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
                result = self.service.set_source_filter_enabled(
                    source,
                    self._required(c, "filter"),
                    str(c.get("action", "toggle")).casefold(),
                )
                return self._result(task, result)
            if self.task_type == "obs.set_source_audio_track":
                c = task.config
                track = int(c.get("track", 1))
                if track not in range(1, 7):
                    raise ValueError("OBS audio track must be from 1 to 6.")
                action = str(c.get("action", "toggle")).casefold()
                if action not in {"enable", "disable", "toggle"}:
                    raise ValueError("OBS audio track action is invalid.")
                result = self.service.set_input_audio_track(
                    self._required(c, "input"),
                    track,
                    action,
                )
                return self._result(task, result)
            if self.task_type == "obs.set_source_transform":
                c = task.config
                result = self.service.set_scene_item_transform(
                    self._required(c, "scene"),
                    self._required(c, "source"),
                    self._source_transform(c, trigger),
                )
                return self._result(task, result)
            if self.task_type == "obs.take_screenshot" and not isinstance(
                trigger.context, dict
            ):
                raise ValueError(
                    "OBS screenshot output requires an active routine context."
                )
            request_type, request_data = self._request(task, trigger)
            result = self.service.request_and_wait(request_type, request_data)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return TaskExecutionResult(task.task_id, task.task_type, False, str(error))
        execution_result = self._result(task, result)
        if execution_result.succeeded and self.task_type == "obs.take_screenshot":
            trigger.context[automation_output_name("screenshot_path")] = str(
                request_data["imageFilePath"]
            )
        return execution_result

    @staticmethod
    def _result(
        task: TaskDefinition,
        result: ObsRequestResult,
    ) -> TaskExecutionResult:
        if result.succeeded:
            detail = f"OBS request {result.request_type} completed."
        else:
            reason = result.comment.strip() or f"error code {result.code}"
            detail = f"OBS request {result.request_type or 'unknown'} failed: {reason}"
        return TaskExecutionResult(
            task.task_id,
            task.task_type,
            result.succeeded,
            detail,
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
            text = render_placeholders(str(c.get("text", "")), trigger.context, strip_values=True)
            return "SetInputSettings", {
                "inputName": self._required(c, "input"),
                "inputSettings": {"text": text},
                "overlay": True,
            }
        if self.task_type == "obs.set_image_source":
            image_file = render_placeholders(
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
        if self.task_type == "obs.set_browser_source_url":
            url = render_placeholders(
                str(c.get("url", "")),
                trigger.context,
                fallback="",
                strip_values=True,
            ).strip()
            if not url:
                raise ValueError("OBS task requires a browser source URL.")
            return "SetInputSettings", {
                "inputName": self._required(c, "input"),
                "inputSettings": {"url": url},
                "overlay": True,
            }
        if self.task_type == "obs.set_media_source_file":
            media_file = render_placeholders(
                str(c.get("file", "")),
                trigger.context,
                fallback="",
                strip_values=True,
            ).strip()
            if not media_file:
                raise ValueError("OBS task requires a media file.")
            return "SetInputSettings", {
                "inputName": self._required(c, "input"),
                "inputSettings": {"local_file": media_file},
                "overlay": True,
            }
        if self.task_type == "obs.set_color_source_color":
            color = render_placeholders(
                str(c.get("color", "")),
                trigger.context,
                fallback="",
                strip_values=True,
            ).strip()
            return "SetInputSettings", {
                "inputName": self._required(c, "input"),
                "inputSettings": {"color": self._obs_color(color)},
                "overlay": True,
            }
        if self.task_type == "obs.restart_media_source":
            return "TriggerMediaInputAction", {
                "inputName": self._required(c, "input"),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
            }
        if self.task_type == "obs.set_transition":
            return "SetCurrentSceneTransition", {
                "transitionName": self._required(c, "transition")
            }
        if self.task_type == "obs.take_screenshot":
            target_type = str(c.get("target_type", "source")).strip().casefold()
            if target_type not in {"source", "scene"}:
                raise ValueError("OBS screenshot target type must be Source or Scene.")
            output_path = render_placeholders(
                str(c.get("file", "")),
                trigger.context,
                fallback="",
                strip_values=True,
            ).strip()
            if not output_path:
                raise ValueError("OBS task requires a screenshot output file path.")
            image_format = str(c.get("image_format", "auto")).strip().casefold()
            if image_format == "auto":
                suffix = output_path.rpartition(".")[2].casefold()
                image_format = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
            if image_format == "jpg":
                image_format = "jpeg"
            if image_format not in {"png", "jpeg"}:
                raise ValueError(
                    "OBS screenshot format must be PNG or JPEG, or match the output file extension."
                )
            return "SaveSourceScreenshot", {
                "sourceName": self._required(c, "target"),
                "imageFormat": image_format,
                "imageFilePath": output_path,
            }
        if self.task_type == "obs.create_record_chapter":
            title = render_placeholders(
                str(c.get("title", "")),
                trigger.context,
                fallback="",
                strip_values=True,
            ).strip()
            if not title:
                raise ValueError("OBS task requires a record chapter title.")
            return "CreateRecordChapter", {"chapterName": title}
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
    def _source_transform(
        config: dict,
        trigger: TriggerEvent,
    ) -> dict[str, object]:
        transform: dict[str, object] = {}
        fields = (
            ("position_x", "positionX", float, False),
            ("position_y", "positionY", float, False),
            ("scale_x", "scaleX", float, False),
            ("scale_y", "scaleY", float, False),
            ("rotation", "rotation", float, False),
            ("crop_top", "cropTop", int, True),
            ("crop_right", "cropRight", int, True),
            ("crop_bottom", "cropBottom", int, True),
            ("crop_left", "cropLeft", int, True),
        )
        for config_key, obs_key, converter, nonnegative in fields:
            raw = render_placeholders(
                str(config.get(config_key, "")),
                trigger.context,
                fallback="",
                strip_values=True,
            ).strip()
            if not raw:
                continue
            try:
                numeric = float(raw)
                if not math.isfinite(numeric):
                    raise ValueError
                if converter is int:
                    if not numeric.is_integer():
                        raise ValueError
                    value = int(numeric)
                else:
                    value = numeric
            except (TypeError, ValueError) as error:
                label = config_key.replace("_", " ")
                raise ValueError(f"OBS {label} must be a valid number.") from error
            if nonnegative and value < 0:
                label = config_key.replace("_", " ")
                raise ValueError(f"OBS {label} cannot be negative.")
            transform[obs_key] = value
        if not transform:
            raise ValueError("Enter at least one OBS source transform value.")
        return transform

    @staticmethod
    def _obs_color(value: str) -> int:
        clean = value.strip()
        if len(clean) != 7 or not clean.startswith("#"):
            raise ValueError("OBS color must use #RRGGBB format.")
        try:
            red = int(clean[1:3], 16)
            green = int(clean[3:5], 16)
            blue = int(clean[5:7], 16)
        except ValueError as error:
            raise ValueError("OBS color must use #RRGGBB format.") from error
        return 0xFF000000 | (blue << 16) | (green << 8) | red

    @staticmethod
    def _required(config: dict, key: str) -> str:
        value = str(config.get(key, "")).strip()
        if not value:
            raise ValueError(f"OBS task requires {key.replace('_', ' ')}.")
        return value


def register_obs_tasks(registry, service: ObsWebSocketService) -> None:
    for task_type in OBS_TASK_LABELS:
        registry.register(ObsTask(service, task_type))
