from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from products.hub.counters.models import CounterDefinition
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.twitch.chatter_history import ChatterHistoryStore
from products.hub.ui.users_page import UsersPage


class UsersPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.chatter_path = root / "chatters.json"
        self.store = ChatterHistoryStore(self.chatter_path)
        self.counters = CounterService(CounterStore(root / "counters"))
        self.opened: list[str] = []
        self.menus: list[str] = []
        self.stream_id = "stream-1"

        def set_group(user_id: str, group: str) -> None:
            self.store.set_manual_group(user_id, group)
            self.store.save()

        self.page = UsersPage(
            self.store,
            self.counters,
            lambda: self.stream_id,
            QWidget(),
            lambda _entry, user_id, _name: self.opened.append(user_id),
            lambda user_id, _name, _message_id: self.menus.append(user_id),
            set_group,
        )
        self.page.resize(1100, 700)
        self.page.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.page.pool.clear()
        self.page.pool.waitForDone(2_000)
        self.page.close()
        self.temp.cleanup()

    def wait_for_counters(self) -> None:
        for _ in range(100):
            QApplication.processEvents()
            if not self.page._counter_pending and not self.page._counter_write_pending:
                return
            QTest.qWait(10)
        self.fail("Counter operation did not finish.")

    def add_user(self, user_id: str, name: str, login: str, **kwargs) -> None:
        self.store.observe_message(user_id, name, user_login=login, **kwargs)

    def test_search_matches_display_name_and_login_case_insensitively(self) -> None:
        self.add_user("1", "JoeViewer", "joe_login")
        self.add_user("2", "Sarah123", "friendly_sarah")
        self.page.refresh(force=True)

        self.page.search.setText("VIEW")
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.page.table.item(0, 0).text(), "JoeViewer")

        self.page.search.setText("sarah")
        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.page.table.item(0, 1).text(), "@friendly_sarah")

        self.page.search.clear()
        self.assertEqual(self.page.table.rowCount(), 2)

    def test_details_distinguish_known_status_from_unknown(self) -> None:
        self.add_user("known", "Known", "known", badges=("moderator", "subscriber"))
        self.add_user("unknown", "Unknown", "unknown")

        self.page.select_user("known")
        self.assertIn("Moderator: Yes", self.page.info.text())
        self.assertIn("VIP: No", self.page.info.text())
        self.assertIn("Subscriber: Yes", self.page.info.text())

        self.page.select_user("unknown")
        self.assertIn("Moderator: Unknown", self.page.info.text())
        self.assertIn("VIP: Unknown", self.page.info.text())
        self.assertIn("Subscriber: Unknown", self.page.info.text())
        self.assertIn("First Seen:", self.page.info.text())
        self.assertIn("Last Seen:", self.page.info.text())

    def test_group_assignment_and_existing_context_menu_are_reused(self) -> None:
        self.add_user("1", "Joe", "joe")
        self.page.select_user("1")
        self.page.group.setCurrentIndex(self.page.group.findData("Bots"))
        self.page._group_changed()

        restored = ChatterHistoryStore(self.chatter_path)
        restored.load()
        self.assertEqual(restored.records["1"].manual_group, "Bots")
        self.assertTrue(restored.is_bot("1"))

        self.page.table.selectRow(0)
        self.page._menu(self.page.table.visualItemRect(self.page.table.item(0, 0)).center())
        self.assertEqual(self.menus, ["1"])

    def test_live_record_updates_refresh_without_replacing_identity(self) -> None:
        self.add_user("stable", "Old Name", "old_login")
        self.page.refresh(force=True)
        self.add_user("stable", "New Name", "new_login")
        self.page.refresh()

        self.assertEqual(self.page.table.rowCount(), 1)
        self.assertEqual(self.page.table.item(0, 0).text(), "New Name")
        self.assertEqual(self.page.table.item(0, 1).text(), "@new_login")

    def test_viewer_counters_display_and_edit_only_viewer_scopes(self) -> None:
        self.add_user("1", "Joe", "joe")
        self.counters.create_counter(CounterDefinition(
            counter_id="farts", display_name="Farts", singular="fart", plural="farts",
            track_viewer_stream_total=True,
        ))
        self.counters.set_value("farts", "channel_total", "100")
        self.counters.set_value("farts", "stream_total", "25", stream_id=self.stream_id)
        self.counters.set_value("farts", "viewer_total", "4", user_id="1")
        self.counters.set_value(
            "farts", "viewer_stream_total", "2", user_id="1", stream_id=self.stream_id
        )
        self.page.select_user("1")
        self.wait_for_counters()

        self.assertEqual(self.page.counter_table.item(0, 1).text(), "4 farts")
        self.assertEqual(self.page.counter_table.item(0, 2).text(), "2 farts")
        self.page.set_counter_value("farts", "viewer_total", "9")
        self.wait_for_counters()
        self.page.set_counter_value("farts", "viewer_stream_total", "7")
        self.wait_for_counters()

        values = self.counters.get_values("farts", user_id="1", stream_id=self.stream_id)
        self.assertEqual(values.viewer_total, Decimal("9"))
        self.assertEqual(values.viewer_stream_total, Decimal("7"))
        self.assertEqual(values.channel_total, Decimal("100"))
        self.assertEqual(values.stream_total, Decimal("25"))

        self.page.set_counter_value("farts", "viewer_total", "1.5")
        self.wait_for_counters()
        values = self.counters.get_values("farts", user_id="1", stream_id=self.stream_id)
        self.assertEqual(values.viewer_total, Decimal("9"))

    def test_decimal_counter_is_exact_and_disabled_scope_is_unavailable(self) -> None:
        self.add_user("1", "Joe", "joe")
        self.counters.create_counter(CounterDefinition(
            counter_id="hydration", display_name="Hydration", singular="litre", plural="litres",
            numeric_type="decimal", display_precision=2, track_viewer_total=True,
            track_viewer_stream_total=False,
        ))
        self.page.select_user("1")
        self.wait_for_counters()
        self.assertEqual(self.page.counter_table.item(0, 2).text(), "Unavailable")

        self.page.set_counter_value("hydration", "viewer_total", "12.125")
        self.wait_for_counters()
        values = self.counters.get_values("hydration", user_id="1", stream_id=self.stream_id)
        self.assertEqual(values.viewer_total, Decimal("12.125"))

        self.page.set_counter_value("hydration", "viewer_stream_total", "7")
        self.wait_for_counters()
        values = self.counters.get_values("hydration", user_id="1", stream_id=self.stream_id)
        self.assertEqual(values.viewer_stream_total, Decimal("0"))

        self.page.set_counter_value("hydration", "viewer_total", "-1")
        self.wait_for_counters()
        values = self.counters.get_values("hydration", user_id="1", stream_id=self.stream_id)
        self.assertEqual(values.viewer_total, Decimal("12.125"))
        self.assertIn("below", self.page.status.text())

    def test_empty_state_and_responsive_splitter(self) -> None:
        self.page.refresh(force=True)
        self.assertTrue(self.page.empty.isVisible())
        self.page.resize(700, 800)
        QApplication.processEvents()
        self.assertEqual(self.page.splitter.orientation(), Qt.Orientation.Vertical)
        self.assertTrue(self.page.table.isColumnHidden(1))
        self.assertTrue(self.page.table.isColumnHidden(4))
        self.page.resize(1000, 700)
        QApplication.processEvents()
        self.assertEqual(self.page.splitter.orientation(), Qt.Orientation.Horizontal)


if __name__ == "__main__":
    unittest.main()
