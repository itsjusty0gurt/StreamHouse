# Offline design checkpoint - 2026-07-20

This checkpoint came from external planning and captures the current long-term
shape for Sally as a local-first AI automation platform.

## Product identity

Sally is not just a Twitch bot. Sally is a modular desktop AI platform where
Twitch is the first service.

Core principles:

- Local-first
- Privacy-first
- Service-based
- Event-driven
- Model agnostic
- Cloud optional

Cloud AI should enhance Sally, not be required for Sally to exist.

## AI routing

Reasoning should not be one giant model call. Sally should route messages
through progressively more expensive stages:

1. Deterministic rule or exact command match
2. Known intent or learned preference match
3. Fast local router
4. Local Qwen response
5. Deep reasoning or optional cloud escalation

Reasoning levels:

- `FAST`: no thinking, short prompt, small token budget, used for commands,
  greetings, simple banter, and local data lookups.
- `STANDARD`: normal local Qwen response for ordinary conversation.
- `DEEP`: thinking enabled, larger budget, used for planning, ambiguous
  multi-step requests, coding, or cloud-escalation decisions.

Default Qwen behavior should be non-thinking. Deep reasoning should be earned
by complexity, risk, ambiguity, or tool requirements.

## Prompt strategy

Avoid one giant system prompt. Build focused prompt modules and include only the
pieces needed for the current request:

- Core personality
- Banter
- Intent classification
- Automation/tool selection
- Memory extraction
- Viewer context
- Cloud escalation

Ordinary Twitch conversation should usually need only personality, recent
conversation, current stream context, and a small amount of relevant viewer
context.

## Performance telemetry

Sally should measure each stage so slowdowns are obvious:

- routing time
- intent confidence
- prompt/context size
- time to first token
- response tokens
- tool-call time
- total response time
- selected reasoning level

This will show whether latency comes from Qwen, memory retrieval, prompt
construction, or tool execution.

## Voice and speech

Voice should be a complete service, not a side feature.

Voice areas:

- Overview
- Input
- Detection
- Transcription
- Activation
- Privacy
- History

Voice MVP:

- microphone selection
- input meter
- voice activity detection
- Faster Whisper transcription
- transcript events
- push-to-talk
- wake phrase
- transcript history
- pause listening while Sally speaks
- voice routines

Early development should assume streamers use headphones. Do not solve echo
cancellation before the simpler loop works.

Voice events should enter the same service/trigger/routine/task pipeline:

- `SpeechStarted`
- `SpeechEnded`
- `TranscriptReady`
- `WakePhraseDetected`
- `TranscriptIgnored`
- `VoiceCommandMatched`

## TTS providers

Sally should have a provider boundary for text-to-speech.

Recommended providers:

- Piper as the default local provider
- Windows TTS as a built-in fallback
- Azure as an optional cloud upgrade
- ElevenLabs as an optional premium provider

Piper fits Sally's default philosophy because it is offline, free, fast, and
low latency. Cloud voices can provide higher quality and emotional expression
without becoming required.

Future emotion tags can flow into TTS:

- `:happy:`
- `:angry:`
- `:sassy:`
- `:sarcastic:`

Cloud providers may express emotions directly. Local providers can approximate
emotion with rate, pitch, pauses, punctuation, and voice selection.

## Memory

Do not build ChatGPT-style memory. Build viewer relationships.

Session memory:

- temporary
- deleted after stream or configured reset
- used for current context, jokes, and conversation continuity

Persistent memory:

- opt-in only
- viewer-controlled
- limited to harmless relationship context
- review-first before becoming approved memory

Viewer commands should include:

- `!sallymemory`
- `!sallymemory on`
- `!sallymemory off`
- `!sallymemory list`
- `!sallymemory clear` or delete flow

Persistent memory should remember only things that improve future interaction,
not entire conversations.

## Training

Training does not mean fine-tuning Qwen at this stage.

Training means:

- intent learning
- preference learning
- local knowledge
- behavior tuning
- unknown-intent resolution

Example:

1. Viewer says "switch to mining scene."
2. Sally does not know the intent.
3. The streamer maps it to a routine.
4. Sally remembers that mapping for future use.

This is more useful and safer than trying to retrain the language model early.

## Long-term differentiators

Sally's strongest identity is:

- local AI through Ollama/Qwen
- offline voice through Faster Whisper and Piper
- privacy-first viewer memory
- service-driven events for Twitch, OBS, Voice, Vision, and future integrations
- adaptive learning through intents and preferences
- deterministic automation before model reasoning

