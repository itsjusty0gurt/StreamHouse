from __future__ import annotations

import re
from collections.abc import Callable
from typing import Mapping
from urllib.error import HTTPError, URLError

from automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from twitch.service import TwitchService


class SendTwitchChatMessageTask:
    task_type = "twitch.send_chat_message"
    TEMPLATE_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*)\}")
    TEMPLATE_VARIABLES = frozenset(
        {
            "args",
            "channel",
            "command",
            "followers",
            "game",
            "target",
            "title",
            "uptime",
            "user",
            "uses",
            "event",
            "event_type",
            "message",
            "input",
            "amount",
            "bits",
            "viewers",
            "tier",
            "reward",
            "reward_id",
            "reward_cost",
            "user_id",
            "target_user_id",
            "message_id",
            "redemption_id",
            "scene",
            "source",
            "output_state",
            "enabled",
            "mute",
            "muted",
            "volume_db",
            "media",
        }
    )

    def __init__(
        self,
        twitch_service: TwitchService,
        variable_resolver: Callable[
            [str, Mapping[str, str]], Mapping[str, str]
        ] | None = None,
    ) -> None:
        self.twitch_service = twitch_service
        self.variable_resolver = variable_resolver

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        template = str(task.config.get("message", "")).strip()
        context = dict(trigger.context)
        if self.variable_resolver is not None:
            context.update(self.variable_resolver(template, context))
        try:
            self.validate_template(template, context)
        except ValueError as error:
            return TaskExecutionResult(
                task_id=task.task_id,
                task_type=task.task_type,
                succeeded=False,
                detail=str(error),
            )
        message = self.render(template, context)[:500]
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
    ) -> None:
        if not template or len(template) > 500:
            raise ValueError("Twitch messages must contain 1-500 characters.")
        allowed = set(allowed_variables)
        unknown = sorted(
            set(cls.TEMPLATE_PATTERN.findall(template))
            - cls.TEMPLATE_VARIABLES
            - allowed
        )
        if unknown:
            raise ValueError(f"Unknown command variable: {{{unknown[0]}}}")

    @classmethod
    def render(cls, template: str, values: Mapping[str, str]) -> str:
        return cls.TEMPLATE_PATTERN.sub(
            lambda match: str(values.get(match.group(1), "--")).strip(),
            template,
        )


TWITCH_TASK_LABELS = {
    SendTwitchChatMessageTask.task_type: "Twitch — Send chat message",
    "twitch.send_pinned_message": "Twitch — Send and pin chat message",
    "twitch.run_commercial": "Twitch — Run commercial",
    "twitch.snooze_ad": "Twitch — Snooze next ad",
    "twitch.update_stream_title": "Twitch — Change stream title",
    "twitch.update_stream_category": "Twitch — Change stream category",
    "twitch.moderate_user": "Twitch — Moderate user",
    "twitch.update_redemption": "Twitch — Fulfill or refund redemption",
}


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
            user_id = self.service.resolve_user_id(render("user", "{user_id}"))
            duration = int(config.get("duration_seconds", 600)) if action == "timeout" else None
            message_id = render("message_id", "{message_id}")
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
                render("reward_id", "{reward_id}"),
                render("redemption_id", "{redemption_id}"),
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
) -> None:
    registry.register(SendTwitchChatMessageTask(service, variable_resolver))
    for task_type in TWITCH_TASK_LABELS:
        if task_type != SendTwitchChatMessageTask.task_type:
            registry.register(
                TwitchAutomationTask(service, task_type, variable_resolver)
            )
