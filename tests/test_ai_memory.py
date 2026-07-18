import unittest
from pathlib import Path

from ai.memory import build_viewer_context
from twitch.chatter_history import ChatterHistoryStore


class ViewerMemoryContextTests(unittest.TestCase):
    @staticmethod
    def qualify(store: ChatterHistoryStore, user_id: str = "1") -> None:
        store.opt_in_memory(user_id, "Viewer")
        for index in range(store.MEMORY_REGULAR_STREAMS):
            store.record_memory_stream(user_id, f"stream-{index}")

    def test_context_only_contains_approved_relevant_memories(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("1", "Viewer")
        self.qualify(store)
        approved = store.add_memory("1", "Enjoys puzzle games", "Preference")
        store.propose_memory("1", "Owns a cat", key="pet")

        context = build_viewer_context(store, "1", "Which puzzle game?")

        self.assertTrue(context["enabled"])
        self.assertEqual(context["memories"][0]["id"], approved["id"])
        self.assertEqual(len(context["memories"]), 1)

    def test_opted_out_viewer_has_no_context(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("1", "Viewer")
        self.qualify(store)
        store.add_memory("1", "Likes jazz")
        store.set_memory_enabled("1", False)

        self.assertEqual(
            build_viewer_context(store, "1", "music"),
            {"enabled": False, "summary": "", "memories": []},
        )


if __name__ == "__main__":
    unittest.main()
