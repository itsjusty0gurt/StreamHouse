# Viewer Memories

Viewer memories support Sally, the default personality inside Streamhouse AI.
In the current Streamhouse Hub UI, Memories is the first internal tab in the AI
remote/control workspace. This leaves the AI page free to grow with additional
tools without adding a new left-navigation button for every feature. Memories
currently reads the Hub-owned local chatter-history store and shows observed
participation statistics and Twitch roles. Streamhouse AI may propose memories
but does not approve or own live viewer authorization.

## Current data

- Twitch user ID and latest observed display name
- first and last seen timestamps
- distinct active days and days present in chatter snapshots
- observed chat-message count
- moderator, VIP, subscriber, bot, regular, or viewer grouping
- follow age when Streamhouse Hub has received a follow timestamp

## AI-memory boundary

Viewer memory is opt-in. A viewer uses `!sallymemory on` in chat; legacy
profiles without an explicit consent record are not eligible. Unconsented
viewer records remain session-only and are omitted when chatter history is
saved. Available commands are:

- `!sallymemory` - explain the feature and controls.
- `!sallymemory on` - consent to daily context and regular qualification.
- `!sallymemory status` - show consent, stream progress, and keynote count.
- `!sallymemory off` - stop memory and erase content while retaining a minimal
  opt-out preference.
- `!sallymemory delete` followed by `!sallymemory confirmdelete` - erase the
  complete viewer profile, consent metadata, runtime context, and associated
  activity-feed entries. Existing Sally backup archives are scrubbed as part of
  confirmed deletion so a later restore cannot silently recreate the profile.

Daily context is capped at 100 messages per opted-in viewer. On startup and
once per minute, Streamhouse Hub compares its last update with the configured local reset
time. Expired context is removed unless it belongs to the Twitch stream that is
still live; a stream crossing the boundary is cleared when it ends.

Persistent keynotes remain locked until the viewer has participated in five
distinct streams after opting in. Extracted keynotes are review-first and
limited to harmless preferences, greetings, recurring channel jokes, and
community context. Sensitive facts and inferred personality or emotional
profiles are rejected.

Each viewer record contains structured memory objects with review status,
confidence, evidence, provenance, creation time, confirmation time, and a stable
fact key. Proposed AI memories remain pending until approved. Same-key changes
are flagged as conflicts; approving the replacement archives the older fact.
Exact duplicates confirm the existing memory instead of creating another copy.

Manual memories can be created, edited, pinned, archived, deleted, exported,
or erased. Each carries a category, source, and creation/update timestamps.
Local model extraction analyzes short RAM-only per-viewer chat batches and may
create evidence-backed pending proposals. No model may silently add approved
memory. The broadcaster, bots, opted-out viewers, and sensitive inferred facts
are excluded.
Per-viewer opt-out rejects pending proposals and prevents the AI context builder
from returning summaries or memories. Context retrieval includes only approved,
non-archived memories and ranks them by prompt relevance, pinning, and confidence.

## Related channel tabs

Streamhouse Hub's **Your Channel** workspace owns operational stream data. In
the current UI, Analytics contains session records for peak viewers, messages,
follows, subscriptions, cheers, and raids. An active session is persisted and
resumed after an application restart rather than being ended when the current
Streamhouse Hub process closes.

Analytics aggregates sessions and viewer participation over all time or the
last 7, 30, or 90 days. It includes stream and engagement totals, new and
returning viewers, regular counts, session comparison, top viewers, CSV/JSON
export, and configurable local session retention. Stream-session history stays
inside Analytics rather than duplicating the same information in a separate
Stream Sessions workspace.

## Viewer timeline

Viewer profiles also track session attendance, message counts per session,
non-content Twitch events, role changes, engagement streaks, private notes, and
manual tags. Timeline filters cover follows, subscriptions, cheers, raids,
rewards, and roles. Duplicate records can be merged, and timeline/session data
can be exported to CSV. Sally does not retain a general raw chat log. A pending
or approved AI proposal retains only its cited evidence excerpts for review.
