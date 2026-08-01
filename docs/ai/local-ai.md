# Streamhouse AI and local AI

Streamhouse AI is the optional local-AI application. Sally is its default AI
personality. Its `products/ai/ai_main.py` entry point,
`products/ai/streamhouse_ai/` application package, heavyweight
`products/ai/engine/` provider package, and `StreamhouseAI.exe` build
output are independent from Hub. See the
[Streamhouse product-family reference](../architecture/product-family.md) for canonical product
boundaries.

Streamhouse AI uses a provider boundary rather than depending directly on one
model. The first provider is Ollama at `http://127.0.0.1:11434`, with
`qwen3:14b` as the default model. These values are saved in Settings and can be
changed without changing application code.

The Ollama provider supports availability/model discovery and non-streaming chat
requests, including optional reasoning and tool definitions. Viewer context is
built separately and contains only approved, non-archived memories.

When **Propose viewer memories from live chat** is enabled, Streamhouse Hub
keeps a small per-viewer rolling buffer in RAM. After the configured number of
meaningful messages (10 by default),
Streamhouse AI asks `qwen3:14b` to analyze that viewer's batch on a background
thread. Hub accepts only constrained JSON proposals whose evidence IDs match
messages in the buffer. Bots, the broadcaster, opted-out viewers, commands, and
very short messages are excluded.

The model cannot approve memories. Valid results enter the Memories review queue
as pending proposals, with confidence, a stable fact key, and exact evidence.
Full chat buffers are discarded after analysis and are never written to disk.
Only the evidence excerpts attached to a pending proposal are persisted so the
streamer can verify it. The prompt rejects inference and highly sensitive facts.
Failures retain the RAM buffer for a delayed retry and never block the UI.

The master **Viewer memory system** setting is off by default. While off, Sally
does not invite opt-ins, collect or restore daily viewer conversations, run
memory extraction, or supply saved viewer memories to reply reasoning. Existing
saved data remains dormant so temporarily disabling the feature is not
destructive. Viewers can still use the off/delete/status commands, while attempts
to opt in report that the streamer has disabled memory. Sally's bounded RAM-only
recent-chat window remains available for immediate co-host conversation and is
discarded when the application closes.

When **Evaluate eligible live chat messages** is enabled, Hub queues every
non-bot viewer message for a background reply decision. Streamhouse AI evaluates
up to eight waiting messages in one local-model request, while preserving one
decision per message. The prompt includes a short RAM-only recent-chat window
plus that viewer's approved memories, allowing fast contextual `reply` or
`ignore` decisions without exposing pending memories as facts.

Hub retains the latest 100 viewer/Sally conversation entries in RAM, and
Streamhouse AI includes only the newest 30 in each model decision. This keeps
longer local recall available while bounding prompt size and response latency.

`hey sally` is a public invocation available to every non-bot chatter. It does
not require follower, subscriber, VIP, moderator, or Regular status. Sally is
instructed to answer unless the invocation is unsafe or abusive spam.

An invocation also opens a short conversation window for that viewer (three
minutes by default and configurable in Settings). During that window, Sally is
given her latest reply to the same viewer so natural follow-ups do not need to
repeat `hey sally`. An answer to a question Sally asked, or a new question from
that viewer, is treated as requiring a response. The window expires rather than
leaving Sally permanently attached to one conversation.

Sally also recognizes direct forms of her name and Twitch's native reply
metadata, so `hey sally` is not a required trigger. The local model receives
recent turn order and can recognize when wording is implicitly directed at her.
Each decision reports whether a Sally/viewer exchange starts, continues, ends,
or remains unchanged. Clear goodbyes, closing thanks, and topic changes therefore
close the conversation immediately instead of waiting only for the timer.

Optional **Co-host interjections** let Sally occasionally add a relevant joke or
useful observation even when nobody directly addresses her. They are off by
default, require at least 88% model confidence, at least six viewer messages
since Sally last spoke, and a separate five-minute cooldown. The same guards
apply when the model merely guesses that an ordinary message implicitly addresses
Sally; a model label cannot bypass the send gate. The prompt requires
interjections to be rare, specific, and worthwhile and tells Sally to stay out
of short acknowledgements, viewer-to-viewer greetings, arguments, and personal
conversations. Third-person discussion of Sally and messages aimed at another
known viewer close the active Sally turn. The feature, cooldown, and minimum
chat activity are configurable in Settings.

Reply drafts and ignore decisions appear under the current **AI > Reply
Review** UI, where the streamer can send, edit, or dismiss them. Automatic
sending is experimental and off by default. If explicitly enabled, Hub only
sends fresh, high-confidence Sally drafts while Twitch chat is connected, and
enforces a configurable minimum gap between replies. Stale model results are
retained for review but cannot be auto-sent. The decision queue and recent-chat
context remain in Hub RAM and are discarded when the current Streamhouse Hub process
closes.

The current **AI > Personality** UI stores the streamer's custom voice and
behavior guidance for Sally.
Separate checkboxes allow mild profanity and strong profanity. Strong language
also enables the mild-language tier. Slurs, hateful language, harassment, and
targeted sexual language remain prohibited at every tier. The selected
personality and language rule are injected into each reply-decision prompt.

The Windows Ollama installer creates a Startup shortcut. Ollama therefore starts
for the signed-in Windows user after login. Streamhouse AI reports whether the
configured endpoint is available and whether the selected model is installed
through the current **Test Local AI** button in Settings.

Local model files are managed by Ollama and are not included in Streamhouse
application backups, diagnostics, Git commits, or Windows release archives.

## Supervised classifier training capture

The optional **Training capture** setting is separate from Sally Memory and is
off by default. When enabled, Sally posts one disclosure per stream/session.
Each tester must type `!sallytrain on`; consent lasts only until Streamhouse Hub closes or
the setting is disabled. `!sallytrain off`, `!sallytrain status`, and
`!sallytrain delete` stop capture, report status, or delete that participant's
saved samples.

For opted-in testers, completed reply decisions are stored locally as pending
classifier examples. The dataset stores a salted participant hash, sanitized
message text, model intent/decision/state, confidence, and review status. It does
not store the Twitch user ID, username, badges, roles, memories, or Sally's reply.
URLs and `@mentions` are replaced. Pending samples expire after 30 days.

**AI > Training** lets the streamer review intent labels, delete individual
samples, or delete the entire dataset. Reviewed labels are future input for a
small intent classifier; this feature captures and labels data but does not yet
automatically retrain or replace Sally's local language model.
