# Changelog

## Unreleased

- structured AI viewer memories with evidence, confidence, provenance, review
  states, duplicate confirmation, contradiction replacement, and per-viewer opt-out
- reviewed-only viewer summaries and relevant-memory retrieval for future AI turns
- provider-neutral local AI client with Ollama discovery/chat support and Local AI
  settings; default configuration targets `qwen3:14b`
- background local-AI viewer-memory reasoning with RAM-only chat buffers,
  evidence-ID validation, sensitive-data guardrails, retry cooldowns, and
  pending-review proposals after a configurable message threshold
- real-time local-AI decisions for every eligible chat message, with bounded
  micro-batching, approved-memory/recent-chat context, a Reply Review workspace,
  and opt-in freshness- and rate-limited automatic replies
- independent encrypted broadcaster and Sally bot Twitch logins, routing channel
  controls through the broadcaster and chat reads/writes through the bot
- AI Personality editor with persistent behavior guidance and explicit mild or
  strong profanity permissions backed by non-overridable abuse guardrails
- Stream Sessions and Analytics moved into dedicated Your Channel tabs
- right-click chatter menus in chat and the chatter list, with persistent local
  Regular/Bot/Viewer grouping and permission-aware timeout, ban, unban, and
  message deletion actions
- chat messages are distinct hoverable moderation targets, using native
  WebEngine context-link metadata instead of unreliable JavaScript callbacks
- bot classification now takes priority over Twitch moderator/VIP/subscriber
  roles in the grouped chatter display
- the broadcaster is excluded from the chatter list, whose heading now shows
  the live chatter total; the redundant chat-message counter was removed
- redundant nested Chat tab and header labels removed from Your Channel
- Twitch EventSub diagnostics moved into Logs > Twitch Events, separate from
  the developer event simulator

## 0.1.0 - 2026-07-12

First release-readiness checkpoint.

- Twitch Device Code authentication, saved encrypted login, EventSub chat,
  activity events, emotes, badges, links, chat sending, and ad controls
- stream companion with grouped chatters and persistent activity history
- AI workspace with viewer Memories, timelines, sessions, and Analytics
- manual memory management, notes, tags, merging, and data exports
- connection health, endpoint-specific recovery, and background API refreshes
- atomic local persistence, explicit migrations, daily/manual backups, restore,
  and sanitized diagnostic bundles
- window/layout persistence, developer simulation tools, and Windows packaging
- package-safe LocalAppData storage with automatic legacy-data migration
- Windows icon/version metadata, clean-environment smoke test, release ZIP, and
  SHA-256 tooling
