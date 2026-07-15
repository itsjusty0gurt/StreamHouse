# Local AI

Sally uses a provider boundary rather than depending directly on one model.
The first provider is Ollama at `http://127.0.0.1:11434`, with `qwen3:14b` as
the default model. These values are saved in Settings and can be changed without
changing application code.

The Ollama provider supports availability/model discovery and non-streaming chat
requests, including optional reasoning and tool definitions. Viewer context is
built separately and contains only approved, non-archived memories.

When **Propose viewer memories from live chat** is enabled, Sally keeps a small
per-viewer rolling buffer in RAM. After the configured number of meaningful
messages (10 by default), `qwen3:14b` analyzes that viewer's batch on a background
thread. Sally accepts only constrained JSON proposals whose evidence IDs match
messages in the buffer. Bots, the broadcaster, opted-out viewers, commands, and
very short messages are excluded.

The model cannot approve memories. Valid results enter the Memories review queue
as pending proposals, with confidence, a stable fact key, and exact evidence.
Full chat buffers are discarded after analysis and are never written to disk.
Only the evidence excerpts attached to a pending proposal are persisted so the
streamer can verify it. The prompt rejects inference and highly sensitive facts.
Failures retain the RAM buffer for a delayed retry and never block the UI.

When **Evaluate eligible live chat messages** is enabled, every non-bot viewer
message is queued for a background reply decision. Sally sends up to eight
waiting messages in one local-model request, while preserving one decision per
message. The prompt includes a short RAM-only recent-chat window plus that
viewer's approved memories, allowing fast contextual `reply` or `ignore`
decisions without exposing pending memories as facts.

Sally retains the latest 100 viewer/Sally conversation entries in RAM and
includes only the newest 30 in each model decision. This keeps longer local
recall available while bounding prompt size and response latency.

`hey sally` is a public invocation available to every non-bot chatter. It does
not require follower, subscriber, VIP, moderator, or Regular status. Sally is
instructed to answer unless the invocation is unsafe or abusive spam.

Reply drafts and ignore decisions appear under **AI > Reply Review**, where the
streamer can send, edit, or dismiss them. Automatic sending is experimental and
off by default. If explicitly enabled, Sally only sends fresh, high-confidence
drafts while Twitch chat is connected, and enforces a configurable minimum gap
between replies. Stale model results are retained for review but cannot be
auto-sent. The decision queue and recent-chat context remain in RAM and are
discarded when Sally closes.

**AI > Personality** stores the streamer's custom voice and behavior guidance.
Separate checkboxes allow mild profanity and strong profanity. Strong language
also enables the mild-language tier. Slurs, hateful language, harassment, and
targeted sexual language remain prohibited at every tier. The selected
personality and language rule are injected into each reply-decision prompt.

The Windows Ollama installer creates a Startup shortcut. Ollama therefore starts
for the signed-in Windows user after login. Sally reports whether the configured
endpoint is available and whether the selected model is installed through the
**Test Local AI** button in Settings.

Local model files are managed by Ollama and are not included in Sally backups,
diagnostics, Git commits, or Windows release archives.
