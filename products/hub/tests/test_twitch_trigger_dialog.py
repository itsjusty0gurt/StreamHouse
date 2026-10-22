import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.twitch.automation_triggers import TwitchEventAutomationTrigger
from products.hub.ui.automation_page import TwitchEventTriggerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_moderation_trigger_editor_uses_friendly_user_and_status_filters() -> None:
    app = _app()
    trigger = TwitchEventAutomationTrigger(
        "trigger-1",
        "routine-1",
        "channel.unban_request.resolve",
        filters={
            "user_login": "viewer",
            "status": "approved",
            "moderator_user_login": "channelmod",
        },
    )
    dialog = TwitchEventTriggerDialog(trigger=trigger)
    dialog.show()
    app.processEvents()

    assert dialog.user_filter_edit.isVisible()
    assert dialog.status_filter_combo.isVisible()
    assert dialog.user_filter_edit.text() == "viewer"
    assert dialog.status_filter_combo.currentData() == "approved"
    assert dialog.filters_edit.text() == "moderator_user_login=channelmod"
    dialog.user_filter_edit.setText("@OtherViewer")
    dialog.status_filter_combo.setCurrentIndex(
        dialog.status_filter_combo.findData("denied")
    )

    assert dialog.values()["filters"] == {
        "moderator_user_login": "channelmod",
        "user_login": "otherviewer",
        "status": "denied",
    }
    dialog.close()
    app.processEvents()


def test_non_moderation_trigger_keeps_friendly_filters_hidden() -> None:
    app = _app()
    dialog = TwitchEventTriggerDialog()
    dialog.event_type_combo.setCurrentIndex(
        dialog.event_type_combo.findData("channel.follow")
    )
    dialog.show()
    app.processEvents()

    assert not dialog.user_filter_edit.isVisible()
    assert not dialog.status_filter_combo.isVisible()
    dialog.close()
    app.processEvents()
