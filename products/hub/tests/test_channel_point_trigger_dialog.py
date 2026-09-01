import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.twitch.automation_triggers import TwitchEventAutomationTrigger
from products.hub.twitch.models import TwitchCustomReward
from products.hub.ui.channel_point_trigger_dialog import ChannelPointRedemptionTriggerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dialog_preserves_missing_saved_reward_and_any_option() -> None:
    app = _app()
    trigger = TwitchEventAutomationTrigger(
        "trigger-1",
        "routine-1",
        "channel.channel_points_custom_reward_redemption.add",
        reward_id="missing-1",
        reward_title="Old reward",
    )
    dialog = ChannelPointRedemptionTriggerDialog(None, None, trigger=trigger)

    assert dialog.reward_combo.itemData(0) == ""
    assert dialog.reward_combo.itemText(0) == "Any Custom Reward"
    assert dialog.values()["reward_id"] == "missing-1"
    assert dialog.values()["reward_title"] == "Old reward"
    assert "preserved" in dialog.status_label.text()
    dialog.close()
    app.processEvents()


def test_discovery_selects_by_stable_id_and_uses_current_title() -> None:
    app = _app()
    trigger = TwitchEventAutomationTrigger(
        "trigger-1",
        "routine-1",
        "channel.channel_points_custom_reward_redemption.add",
        reward_id="reward-1",
        reward_title="Old title",
    )
    dialog = ChannelPointRedemptionTriggerDialog(None, None, trigger=trigger)
    dialog._request_id = 7

    dialog._rewards_loaded(
        7,
        [TwitchCustomReward("reward-1", "Renamed reward", 500)],
        None,
    )

    assert dialog.values()["reward_id"] == "reward-1"
    assert dialog.values()["reward_title"] == "Renamed reward"
    dialog.close()
    app.processEvents()


def test_stale_discovery_response_is_ignored() -> None:
    app = _app()
    dialog = ChannelPointRedemptionTriggerDialog(None, None)
    dialog._request_id = 3

    dialog._rewards_loaded(
        2,
        [TwitchCustomReward("reward-1", "Stale reward", 100)],
        None,
    )

    assert dialog.reward_combo.findData("reward-1") == -1
    dialog.close()
    app.processEvents()
