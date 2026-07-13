# Viewer Memories

Memories is the first internal tab in the primary AI workspace. This leaves the
AI page free to grow with additional tools without adding a new left-navigation
button for every feature. Memories currently reads the local chatter-history
store and shows observed participation statistics and Twitch roles.

## Current data

- Twitch user ID and latest observed display name
- first and last seen timestamps
- distinct active days and days present in chatter snapshots
- observed chat-message count
- moderator, VIP, subscriber, bot, regular, or viewer grouping
- follow age when Sally has received a follow timestamp

## AI-memory boundary

Each viewer record reserves a list of structured memory objects. Automatic AI
memory creation is intentionally disabled until the product has controls for
consent, provenance, confidence, editing, deletion, and complete opt-out. Raw
chat transcripts are not stored by the chatter-history feature.

Manual memories can be created, edited, pinned, archived, deleted, exported,
or erased. Each carries a category, source, and creation/update timestamps.
Automated extraction remains disabled.

## Related AI tabs

The Stream Sessions tab records peak viewers, messages, follows,
subscriptions, cheers, and raids. An active session is persisted and resumed
after an application restart rather than being ended when Sally closes.

The Analytics tab aggregates sessions and viewer participation over all time or
the last 7, 30, or 90 days. It includes stream and engagement totals, new and
returning viewers, regular counts, session comparison, top viewers, CSV/JSON
export, and configurable local session retention.

## Viewer timeline

Viewer profiles also track session attendance, message counts per session,
non-content Twitch events, role changes, engagement streaks, private notes, and
manual tags. Timeline filters cover follows, subscriptions, cheers, raids,
rewards, and roles. Duplicate records can be merged, and timeline/session data
can be exported to CSV. Sally stores message counts but not raw message text.
