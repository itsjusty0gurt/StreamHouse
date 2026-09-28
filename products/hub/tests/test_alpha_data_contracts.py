from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import TaskDefinition
from products.hub.automation.queues import AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.counters.models import CounterDefinition
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.channel_information import (
    ChannelInformation,
    ChannelInformationStore,
    SocialLink,
)
from products.hub.twitch.chatter_history import ChatterHistoryStore
from products.hub.twitch.commands import TwitchCommandTriggerStore


def test_representative_alpha_contract_survives_save_restart_and_reload() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)

        queues = AutomationQueueStore(root / "automation" / "queues.json")
        queues.load()
        custom_queue = queues.add("Alerts")

        routines = RoutineStore(root / "automation" / "routines.json")
        group = routines.add_group("Stream Events")
        child = routines.add("Child")
        parent = routines.add(
            "Raid Welcome",
            group_id=group.group_id,
            queue_id=custom_queue.queue_id,
        )
        condition = routines.add_task(
            parent.routine_id,
            task_type="core.if",
            name="If enabled",
            config={"left": "yes", "operator": "equals", "right": "yes"},
            then_tasks=[
                TaskDefinition(
                    "nested-run-task",
                    "core.run_routine",
                    "Run child",
                    {"routine_id": child.routine_id},
                )
            ],
            else_tasks=[
                TaskDefinition("nested-fallback-task", "core.end_routine", "Stop")
            ],
        )

        commands = TwitchCommandTriggerStore(
            root / "twitch" / "commands.json", routines
        )
        commands.load()
        command = commands.attach_routine(parent.routine_id, "raidhello", "Welcome!")
        twitch = TwitchEventTriggerStore(
            root / "twitch" / "event_triggers.json",
            routines,
            root / "twitch" / "first_message_state.json",
        )
        event = twitch.add(
            parent.routine_id,
            "channel.raid",
            filters={"direction": "incoming"},
        )

        variables = CustomVariableStore(root / "automation" / "variables.json")
        variables.set("global", "greeting", "Hello", data_type="text")
        variables.set("session", "transient", "not durable", data_type="text")

        counter_service = CounterService(CounterStore(root / "counters"))
        counter_service.create_counter(
            CounterDefinition(
                counter_id="hype",
                display_name="Hype",
                singular="point",
                plural="points",
                track_channel_total=True,
                track_stream_total=True,
                track_viewer_total=True,
                track_viewer_stream_total=True,
                numeric_type="decimal",
                reset_value=Decimal("0"),
                minimum=Decimal("0"),
                display_precision=3,
            )
        )
        assert counter_service.set_value("hype", "channel_total", "12.345").status == "success"
        assert counter_service.set_value(
            "hype", "stream_total", "4.125", stream_id="stream-123"
        ).status == "success"
        assert counter_service.set_value(
            "hype", "viewer_total", "3.375", user_id="viewer-1"
        ).status == "success"
        assert counter_service.set_value(
            "hype",
            "viewer_stream_total",
            "1.625",
            user_id="viewer-1",
            stream_id="stream-123",
        ).status == "success"
        assert counter_service.set_value(
            "hype", "viewer_total", "8.875", user_id="viewer-2"
        ).status == "success"

        channel = ChannelInformation(schedule="Friday 8 PM", rules="Be kind.")
        channel.social_links["discord"] = SocialLink(
            True, "https://discord.example/streamhouse"
        )
        ChannelInformationStore(
            root / "twitch" / "channel-information.json"
        ).save(channel)

        users = ChatterHistoryStore(root / "memory" / "twitch_chatters.json")
        observed = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
        users.observe_message(
            "viewer-1", "Viewer One", observed, user_login="viewer_one"
        )
        users.set_manual_group("viewer-1", "Regulars")
        users.observe_message(
            "viewer-1",
            "Renamed Viewer",
            observed + timedelta(minutes=1),
            user_login="renamed_viewer",
        )
        users.observe_message(
            "viewer-2", "Helper Bot", observed, is_bot=True, user_login="helperbot"
        )
        users.save()

        queues.update(custom_queue.queue_id, name="Priority Alerts")
        routines.update_group(group.group_id, name="Community Events")
        routines.update(parent.routine_id, name="Incoming Raid Welcome")

        reloaded_queues = AutomationQueueStore(queues.path)
        reloaded_queues.load()
        reloaded_routines = RoutineStore(routines.path)
        reloaded_routines.load()
        reloaded_commands = TwitchCommandTriggerStore(commands.path, reloaded_routines)
        reloaded_commands.load()
        reloaded_twitch = TwitchEventTriggerStore(
            twitch.path, reloaded_routines, twitch.first_message_state_path
        )
        reloaded_twitch.load()

        saved_parent = reloaded_routines.get(parent.routine_id)
        assert saved_parent is not None
        assert saved_parent.name == "Incoming Raid Welcome"
        assert saved_parent.group_id == group.group_id
        assert saved_parent.queue_id == custom_queue.queue_id
        assert saved_parent.trigger_ids == (command.trigger_id, event.trigger_id)
        saved_condition = next(
            task for task in saved_parent.tasks if task.task_id == condition.task_id
        )
        assert saved_condition.then_tasks[0].task_id == "nested-run-task"
        assert (
            saved_condition.then_tasks[0].config["routine_id"]
            == child.routine_id
        )
        assert reloaded_queues.get(custom_queue.queue_id).name == "Priority Alerts"
        assert reloaded_commands.get(command.trigger_id).routine_id == parent.routine_id
        assert reloaded_twitch.get(event.trigger_id).routine_id == parent.routine_id

        generated_ids = {item.trigger_id for item in reloaded_commands.triggers}
        reloaded_commands.load()
        assert {item.trigger_id for item in reloaded_commands.triggers} == generated_ids

        reloaded_variables = CustomVariableStore(variables.path)
        reloaded_variables.load()
        assert reloaded_variables.global_values == {"greeting": "Hello"}
        assert reloaded_variables.session_values == {}

        reloaded_counters = CounterService(CounterStore(root / "counters"))
        first = reloaded_counters.get_values(
            "hype", user_id="viewer-1", stream_id="stream-123"
        )
        second = reloaded_counters.get_values(
            "hype", user_id="viewer-2", stream_id="stream-123"
        )
        assert first.channel_total == Decimal("12.345")
        assert first.stream_total == Decimal("4.125")
        assert first.viewer_total == Decimal("3.375")
        assert first.viewer_stream_total == Decimal("1.625")
        assert second.viewer_total == Decimal("8.875")
        assert second.viewer_stream_total == Decimal("0")

        reloaded_users = ChatterHistoryStore(users.path)
        reloaded_users.load()
        assert set(reloaded_users.records) == {"viewer-1", "viewer-2"}
        assert reloaded_users.records["viewer-1"].user_name == "Renamed Viewer"
        assert reloaded_users.records["viewer-1"].manual_group == "Regulars"
        assert reloaded_users.records["viewer-2"].is_bot

        loaded_channel = ChannelInformationStore(
            root / "twitch" / "channel-information.json"
        ).load()
        assert loaded_channel.schedule == "Friday 8 PM"
        assert loaded_channel.social_links["discord"].enabled_in_socials
