from __future__ import annotations

from typing import Any

from twitch.chatter_history import ChatterHistoryStore


def build_viewer_context(
    store: ChatterHistoryStore,
    user_id: str,
    prompt: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Build the reviewed-only viewer context supplied to a future AI turn."""
    record = store.records.get(user_id)
    if record is None or not store.can_create_keynotes(user_id):
        return {"enabled": False, "summary": "", "memories": []}
    memories = store.relevant_memories(user_id, prompt, limit)
    return {
        "enabled": True,
        "summary": store.viewer_summary(user_id),
        "memories": [
            {
                "id": str(memory.get("id", "")),
                "text": str(memory.get("text", "")),
                "category": str(memory.get("category", "General")),
                "confidence": float(memory.get("confidence", 1.0)),
            }
            for memory in memories
        ],
    }
