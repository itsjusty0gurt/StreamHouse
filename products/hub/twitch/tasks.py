from __future__ import annotations

import re
from collections.abc import Callable
from typing import Mapping
from urllib.error import HTTPError, URLError

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.variable_outputs import automation_output_name, output_id
from products.hub.automation.variable_registry import (
    PLACEHOLDER_PATTERN,
    VariableDefinition,
    VariableRegistry,
    render_placeholders,
)
from products.hub.twitch.channel_information import (
    CHANNEL_INFORMATION_FIELD_LABELS,
    ChannelInformationStore,
)
from products.hub.twitch.service import TwitchService
from shared.streamhouse_runtime.logger import Logger


class SendTwitchChatMessageTask:
    task_type = "twitch.send_chat_message"
    TEMPLATE_PATTERN = PLACEHOLDER_PATTERN

    def __init__(
        self,
        twitch_service: TwitchService,
        variable_registry: VariableRegistry | None = None,
    ) -> None:
        self.twitch_service = twitch_service
        self.variable_registry = variable_registry

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        template = str(task.config.get("message", "")).strip()
        context = dict(trigger.context)
        if self.variable_registry is not None:
            for name in self.TEMPLATE_PATTERN.findall(template):
                if name in context:
                    continue
                snapshot = self.variable_registry.resolve(name, context)
                if snapshot is not None and snapshot.available:
                    context[name] = snapshot.display_value
        try:
            self.validate_template(
                template,
                context,
                registry=self.variable_registry,
            )
        except ValueError as error:
            return TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail=str(error),
            )
        missing = sorted(
            name
            for name in set(self.TEMPLATE_PATTERN.findall(template))
            if name not in context
        )
        unavailable = sorted(
            name
            for name in set(self.TEMPLATE_PATTERN.findall(template))
            if str(
                context.get(
                    automation_output_name(name, "status")
                    if name.startswith("automation.")
                    else "",
                    "",
                )
            ).casefold()
            in {"missing", "unavailable", "error"}
        )
        blocked = missing or unavailable
        if blocked:
            return TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail=f"Message not sent because {{{blocked[0]}}} is unavailable.",
            )
        message = self.render(template, context)[:500]
        if not message.strip():
            return TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail="Message not sent because the rendered message is empty.",
            )
        succeeded = self.twitch_service.send_message(
            message,
            as_bot=bool(task.config.get("as_bot", True)),
        )
        return TaskExecutionResult(
            task_id=task.task_id,
            task_type=task.task_type,
            succeeded=succeeded,
            detail="Sent Twitch chat message." if succeeded else "Twitch send failed.",
        )

    @classmethod
    def validate_template(
        cls,
        template: str,
        allowed_variables: Mapping[str, object] | set[str] | tuple[str, ...] = (),
        *,
        registry: VariableRegistry | None = None,
        extra_definitions: tuple[VariableDefinition, ...] = (),
    ) -> None:
        if not template or len(template) > 500:
            raise ValueError("Twitch messages must contain 1-500 characters.")
        malformed = next(
            (
                token
                for token in re.findall(r"\{([^{}]+)\}", template)
                if not PLACEHOLDER_PATTERN.fullmatch(f"{{{token}}}")
            ),
            "",
        )
        if malformed:
            raise ValueError(f"Invalid canonical command variable: {{{malformed}}}")
        allowed = set(allowed_variables)
        allowed.update(definition.name for definition in extra_definitions)
        if registry is not None:
            allowed.update(definition.name for definition in registry.definitions())
        unknown = sorted(
            name
            for name in set(cls.TEMPLATE_PATTERN.findall(template))
            if registry is not None and name not in allowed
        )
        if unknown:
            raise ValueError(f"Unknown command variable: {{{unknown[0]}}}")

    @classmethod
    def render(cls, template: str, values: Mapping[str, str]) -> str:
        return render_placeholders(template, values, strip_values=True)


TWITCH_TASK_LABELS = {
    SendTwitchChatMessageTask.task_type: "Twitch — Send chat message",
    "twitch.resolve_user": "Twitch — Resolve user",
    "twitch.get_stream_information": "Twitch — Get stream information",
    "twitch.get_channel_information": "Twitch — Get channel information",
    "twitch.get_follow_relationship": "Twitch — Get follow relationship",
    "twitch.build_command_list": "Twitch — Build command list",
    "twitch.get_channel_information_field": "Twitch — Get Channel Information field",
    "twitch.build_social_links_message": "Twitch — Build social links message",
    "twitch.send_pinned_message": "Twitch — Send and pin chat message",
    "twitch.run_commercial": "Twitch — Run commercial",
    "twitch.snooze_ad": "Twitch — Snooze next ad",
    "twitch.update_stream_title": "Twitch — Change stream title",
    "twitch.update_stream_category": "Twitch — Change stream category",
    "twitch.moderate_user": "Twitch — Moderate user",
    "twitch.update_redemption": "Twitch — Fulfill or refund redemption",
}

TWITCH_INFORMATION_TASK_TYPES = frozenset(
    {
        "twitch.resolve_user",
        "twitch.get_stream_information",
        "twitch.get_channel_information",
        "twitch.get_follow_relationship",
        "twitch.get_channel_information_field",
        "twitch.build_social_links_message",
    }
)


def _mutable_context(trigger: TriggerEvent) -> dict[str, str]:
    if not isinstance(trigger.context, dict):
        raise ValueError("Twitch task output requires a mutable routine context.")
    return trigger.context


def _publish(context: dict[str, str], values: Mapping[str, object]) -> None:
    context.update(
        {automation_output_name(name): str(value) for name, value in values.items()}
    )


def _output(context: Mapping[str, str], name: str, default: str = "") -> str:
    return str(context.get(automation_output_name(name), default))


def _set_output(context: dict[str, str], name: str, value: object) -> None:
    context[automation_output_name(name)] = str(value)


def _task_result(task: TaskDefinition, detail: str, succeeded: bool = True):
    return TaskExecutionResult(task.task_id, task.task_type, succeeded, detail)


class ResolveTwitchUserTask:
    task_type = "twitch.resolve_user"

    def __init__(self, service: TwitchService) -> None:
        self.service = service

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        reference = SendTwitchChatMessageTask.render(
            str(task.config.get("reference", "{command.target}")), context
        ).strip().lstrip("@")
        if not reference or reference == "--" or PLACEHOLDER_PATTERN.fullmatch(reference):
            reference = str(context.get("user.id", context.get("user_id", ""))).strip()
        _publish(context,
            {
                "target_user_id": "",
                "target_login": "",
                "target_display_name": "",
                "account_created_at": "",
                "user_lookup_status": "error",
            }
        )
        try:
            user = self.service.resolve_user(reference)
        except ValueError as error:
            if "not found" in str(error).casefold():
                _set_output(context, "user_lookup_status", "not_found")
                return _task_result(task, "Twitch user was not found.")
            Logger.warning(f"Could not resolve Twitch user: {error}", source="TWITCH")
            return _task_result(task, "Twitch user lookup is unavailable.")
        except (HTTPError, URLError, OSError) as error:
            Logger.warning(f"Could not resolve Twitch user: {error}", source="TWITCH")
            return _task_result(task, "Twitch user lookup is unavailable.")
        _publish(context,
            {
                "target_user_id": str(user.get("id", "")),
                "target_login": str(user.get("login", "")),
                "target_display_name": str(
                    user.get("display_name", user.get("login", reference))
                ),
                "account_created_at": str(user.get("created_at", "")),
                "user_lookup_status": "found",
            }
        )
        return _task_result(task, "Resolved Twitch user.")


class GetStreamInformationTask:
    task_type = "twitch.get_stream_information"

    def __init__(self, service: TwitchService) -> None:
        self.service = service

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        _publish(context,
            {
                "stream_status": "error",
                "is_live": "false",
                "stream_started_at": "",
                "stream_title": "",
                "stream_category": "",
                "stream_id": "",
                "stream_viewers": "0",
                "stream_game_id": "",
            }
        )
        try:
            stream = self.service.get_stream_information()
        except (HTTPError, URLError, OSError, ValueError) as error:
            Logger.warning(f"Could not retrieve Twitch stream information: {error}", source="TWITCH")
            return _task_result(task, "Twitch stream information is unavailable.")
        if not stream:
            _set_output(context, "stream_status", "offline")
            return _task_result(task, "The Twitch channel is offline.")
        _publish(context,
            {
                "stream_status": "live",
                "is_live": "true",
                "stream_started_at": str(stream.get("started_at", "")),
                "stream_title": str(stream.get("title", "")),
                "stream_category": str(stream.get("game_name", "")),
                "stream_id": str(stream.get("id", "")),
                "stream_viewers": str(stream.get("viewer_count", 0)),
                "stream_game_id": str(stream.get("game_id", "")),
            }
        )
        return _task_result(task, "Retrieved live Twitch stream information.")


class GetChannelInformationTask:
    task_type = "twitch.get_channel_information"

    def __init__(self, service: TwitchService) -> None:
        self.service = service

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        _publish(context,
            {
                "channel_info_status": "error",
                "title_status": "error",
                "category_status": "error",
                "stream_title": "",
                "stream_category": "",
                "stream_game_id": "",
            }
        )
        try:
            channel = self.service.get_channel_information()
        except (HTTPError, URLError, OSError, ValueError) as error:
            Logger.warning(f"Could not retrieve Twitch channel information: {error}", source="TWITCH")
            return _task_result(task, "Twitch channel information is unavailable.")
        if not channel:
            _publish(context,
                {
                    "channel_info_status": "unavailable",
                    "title_status": "unavailable",
                    "category_status": "unset",
                }
            )
            return _task_result(task, "Twitch channel information was empty.")
        title = str(channel.get("title", "")).strip()
        category = str(channel.get("game_name", "")).strip()
        _publish(context,
            {
                "channel_info_status": "available",
                "title_status": "available" if title else "unavailable",
                "category_status": "set" if category else "unset",
                "stream_title": title,
                "stream_category": category,
                "stream_game_id": str(channel.get("game_id", "")),
            }
        )
        return _task_result(task, "Retrieved Twitch channel information.")


class GetFollowRelationshipTask:
    task_type = "twitch.get_follow_relationship"

    def __init__(self, service: TwitchService) -> None:
        self.service = service

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        user_id = SendTwitchChatMessageTask.render(
            str(task.config.get("user_id", "{automation.target_user_id}")), context
        ).strip()
        _publish(context,
            {
                "is_following": "false",
                "followed_at": "",
                "follow_status": "error",
                "channel_display_name": self.service.channel_display_name(),
            }
        )
        if not user_id:
            if _output(context, "user_lookup_status") == "not_found":
                _set_output(context, "follow_status", "user_not_found")
                return _task_result(task, "Follow lookup skipped because the user was not found.")
            return _task_result(task, "Follow lookup skipped because user lookup failed.")
        if user_id == getattr(self.service, "broadcaster_user_id", ""):
            _set_output(context, "follow_status", "broadcaster")
            return _task_result(task, "The selected user is the broadcaster.")
        try:
            relationship = self.service.get_follow_relationship(user_id)
        except PermissionError:
            _set_output(context, "follow_status", "missing_scope")
            return _task_result(task, "Follow information permission has not been granted.")
        except HTTPError as error:
            _set_output(context, "follow_status", "missing_scope" if error.code in {401, 403} else "error")
            Logger.warning(f"Could not retrieve Twitch follow information: HTTP {error.code}", source="TWITCH")
            return _task_result(task, "Twitch follow information is unavailable.")
        except (URLError, OSError, ValueError) as error:
            Logger.warning(f"Could not retrieve Twitch follow information: {error}", source="TWITCH")
            return _task_result(task, "Twitch follow information is unavailable.")
        if not relationship:
            _set_output(context, "follow_status", "not_following")
            return _task_result(task, "The selected user is not following the channel.")
        _publish(context,
            {
                "is_following": "true",
                "followed_at": str(relationship.get("followed_at", "")),
                "follow_status": "following",
            }
        )
        return _task_result(task, "Retrieved Twitch follow relationship.")


class BuildCommandListTask:
    task_type = "twitch.build_command_list"
    PERMISSION_RANK = {
        "everyone": 0,
        "subscriber": 1,
        "vip": 2,
        "moderator": 3,
        "broadcaster": 4,
    }

    def __init__(self, command_provider: Callable[[], object]) -> None:
        self.command_provider = command_provider

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        _publish(context, {"command_list": "", "command_list_status": "empty"})
        role = str(context.get("viewer_permission", "everyone")).casefold()
        rank = self.PERMISSION_RANK.get(role, 0)
        try:
            store = self.command_provider()
            commands = getattr(store, "triggers", ())
            names = sorted(
                {
                    f"!{command.name}"
                    for command in commands
                    if command.enabled
                    and self.PERMISSION_RANK.get(command.permission, 99) <= rank
                }
            )
        except Exception as error:
            Logger.warning(f"Could not build Twitch command list: {error}", source="TWITCH")
            _set_output(context, "command_list_status", "error")
            return _task_result(task, "Command list is unavailable.")
        try:
            requested_limit = int(task.config.get("maximum_characters", 450))
        except (TypeError, ValueError):
            requested_limit = 450
        limit = min(max(requested_limit, 50), 480)
        selected: list[str] = []
        for name in names:
            candidate = ", ".join((*selected, name))
            if len(candidate) > limit:
                break
            selected.append(name)
        _set_output(context, "command_list", ", ".join(selected))
        _set_output(context, "command_list_status", "available" if selected else "empty")
        return _task_result(task, f"Listed {len(selected)} enabled Twitch commands.")


class GetChannelInformationFieldTask:
    task_type = "twitch.get_channel_information_field"

    def __init__(self, store_provider: Callable[[], ChannelInformationStore]) -> None:
        self.store_provider = store_provider

    @staticmethod
    def output_name(config: Mapping[str, object]) -> str:
        field_id = str(config.get("field", "")).strip().casefold()
        requested = str(config.get("output_variable", "")).strip()
        return automation_output_name(requested or field_id)

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        field_id = str(task.config.get("field", "")).strip().casefold()
        label = CHANNEL_INFORMATION_FIELD_LABELS.get(field_id)
        if label is None:
            return _task_result(task, "Choose a valid Channel Information field.", False)
        try:
            output_name = self.output_name(task.config)
        except ValueError as error:
            return _task_result(task, str(error), False)
        context.update(
            {
                output_name: "",
                automation_output_name(output_name, "status"): "unavailable",
                automation_output_name("channel_information_available"): "false",
                automation_output_name("channel_information_status"): "unavailable",
            }
        )
        try:
            value = self.store_provider().field_value(field_id)
        except (OSError, ValueError) as error:
            _set_output(context, "channel_information_status", "error")
            context[automation_output_name(output_name, "status")] = "error"
            return _task_result(task, f"Could not read {label}: {error}", False)
        if not value:
            return _task_result(
                task,
                f"Configure {label} in Twitch > Channel Information before running this task.",
                False,
            )
        context.update(
            {
                output_name: value,
                automation_output_name(output_name, "status"): "available",
                automation_output_name("channel_information_available"): "true",
                automation_output_name("channel_information_status"): "available",
            }
        )
        return _task_result(task, f"Loaded {label} from Channel Information.")


class BuildSocialLinksMessageTask:
    task_type = "twitch.build_social_links_message"

    def __init__(self, store_provider: Callable[[], ChannelInformationStore]) -> None:
        self.store_provider = store_provider

    @staticmethod
    def output_name(config: Mapping[str, object]) -> str:
        requested = str(config.get("output_variable", "")).strip()
        return automation_output_name(requested or "social_links_message")

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        context = _mutable_context(trigger)
        try:
            output_name = self.output_name(task.config)
        except ValueError as error:
            return _task_result(task, str(error), False)
        context.update(
            {
                output_name: "",
                automation_output_name(output_name, "status"): "unavailable",
                automation_output_name("channel_information_available"): "false",
                automation_output_name("channel_information_status"): "unavailable",
            }
        )
        try:
            maximum = int(task.config.get("maximum_characters", 480))
            message = self.store_provider().build_social_links_message(maximum)
        except (OSError, TypeError, ValueError) as error:
            _set_output(context, "channel_information_status", "error")
            context[automation_output_name(output_name, "status")] = "error"
            return _task_result(task, f"Could not build social links: {error}", False)
        if not message:
            return _task_result(
                task,
                "Select at least one valid link in Twitch > Channel Information before running this task.",
                False,
            )
        context.update(
            {
                output_name: message,
                automation_output_name(output_name, "status"): "available",
                automation_output_name("channel_information_available"): "true",
                automation_output_name("channel_information_status"): "available",
            }
        )
        return _task_result(task, "Built a Twitch-ready social links message.")


class TwitchAutomationTask:
    def __init__(
        self,
        service: TwitchService,
        task_type: str,
        variable_resolver: Callable[
            [str, Mapping[str, str]], Mapping[str, str]
        ] | None = None,
    ) -> None:
        self.service = service
        self.task_type = task_type
        self.variable_resolver = variable_resolver

    def execute(
        self, task: TaskDefinition, trigger: TriggerEvent
    ) -> TaskExecutionResult:
        try:
            detail = self._execute(task, trigger)
            succeeded = True
        except (HTTPError, URLError, OSError, TypeError, ValueError) as error:
            succeeded = False
            detail = str(error)
        return TaskExecutionResult(
            task.task_id, task.task_type, succeeded, detail
        )

    def _execute(self, task: TaskDefinition, trigger: TriggerEvent) -> str:
        config = task.config
        def render(key: str, default: str = "") -> str:
            template = str(config.get(key, default))
            context = dict(trigger.context)
            if self.variable_resolver is not None:
                context.update(self.variable_resolver(template, context))
            return SendTwitchChatMessageTask.render(template, context).strip()
        if self.task_type == "twitch.send_pinned_message":
            message = render("message")[:500]
            SendTwitchChatMessageTask.validate_template(
                str(config.get("message", "")),
                trigger.context,
            )
            sent, pinned = self.service.send_pinned_message(message)
            if not sent or not pinned:
                raise ValueError(
                    "Twitch could not send and pin the message."
                )
            return "Sent and pinned Twitch chat message."
        if self.task_type == "twitch.run_commercial":
            result = self.service.run_commercial(int(config.get("length", 30)))
            return str(result.get("message", "Commercial started.")) or "Commercial started."
        if self.task_type == "twitch.snooze_ad":
            self.service.snooze_next_ad()
            return "Snoozed the next ad by five minutes."
        if self.task_type == "twitch.update_stream_title":
            title = render("title")[:140]
            if not title:
                raise ValueError("Enter a stream title.")
            self.service.update_stream_title(title)
            return f'Changed the Twitch stream title to "{title}".'
        if self.task_type == "twitch.update_stream_category":
            category = render("category")
            if not category:
                raise ValueError("Enter a Twitch category name.")
            selected = self.service.update_stream_category(category)
            return f'Changed the Twitch stream category to "{selected}".'
        if self.task_type == "twitch.moderate_user":
            action = str(config.get("action", "timeout"))
            user_id = self.service.resolve_user_id(render("user", "{user.id}"))
            duration = int(config.get("duration_seconds", 600)) if action == "timeout" else None
            message_id = render("message_id", "{chat.message_id}")
            succeeded = self.service.moderate_user(
                action,
                user_id,
                duration=duration,
                reason=render("reason"),
                message_id="" if message_id == "--" else message_id,
            )
            if not succeeded:
                raise ValueError("Twitch moderation action failed.")
            return f"Completed Twitch moderation action: {action}."
        if self.task_type == "twitch.update_redemption":
            status = "FULFILLED" if config.get("action", "fulfill") == "fulfill" else "CANCELED"
            self.service.update_redemption_status(
                render("reward_id", "{event.reward_id}"),
                render("redemption_id", "{event.redemption_id}"),
                status,
            )
            return "Fulfilled Twitch redemption." if status == "FULFILLED" else "Refunded Twitch redemption."
        raise ValueError(f"Unsupported Twitch task type: {self.task_type}")


def register_twitch_tasks(
    registry,
    service: TwitchService,
    variable_resolver: Callable[
        [str, Mapping[str, str]], Mapping[str, str]
    ] | None = None,
    command_provider: Callable[[], object] | None = None,
    channel_information_provider: Callable[[], ChannelInformationStore] | None = None,
    variable_registry: VariableRegistry | None = None,
) -> None:
    registry.register(SendTwitchChatMessageTask(service, variable_registry))
    registry.register(ResolveTwitchUserTask(service))
    registry.register(GetStreamInformationTask(service))
    registry.register(GetChannelInformationTask(service))
    registry.register(GetFollowRelationshipTask(service))
    registry.register(BuildCommandListTask(command_provider or (lambda: None)))
    information_provider = channel_information_provider or ChannelInformationStore
    registry.register(GetChannelInformationFieldTask(information_provider))
    registry.register(BuildSocialLinksMessageTask(information_provider))
    for task_type in TWITCH_TASK_LABELS:
        if task_type not in {
            SendTwitchChatMessageTask.task_type,
            ResolveTwitchUserTask.task_type,
            GetStreamInformationTask.task_type,
            GetChannelInformationTask.task_type,
            GetFollowRelationshipTask.task_type,
            BuildCommandListTask.task_type,
            GetChannelInformationFieldTask.task_type,
            BuildSocialLinksMessageTask.task_type,
        }:
            registry.register(
                TwitchAutomationTask(service, task_type, variable_resolver)
            )
