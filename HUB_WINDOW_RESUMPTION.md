# Hub Window Resumption (PR B)

Depends on cirel94june/memory-hub PR #44, merge e097b8ea1a7ef6cb88e0949b8ab661da3677d489.

## Contract

- POST /api/window/context (not GET), using the existing Hub Authorization header.
- Send ai_id, chat_id, thread_id, max_turns=10, max_chars=20000.
- Upload message_id, sender_id, sender_type, thread_id, reply_to_id to capture/log.
- message_id identifies the triggering incoming Telegram message, not the reply.
- Normal successful-reply capture still uploads only Telegram-confirmed delivered text.
- Existing capture callers without metadata remain compatible.

## Behavior

An empty local window registers for recovery before adding the current message.
Warm history is left alone. Listener-only events do not trigger a network call,
but keep recovery pending for the next reply. Recovery uses a 3-second outer
deadline, at most one in-flight recovery per process, and a 60-second retry
cooldown. A late network result never mutates live history. This does not
cancel the underlying HTTP call. Failed recovery does not block normal replies.

Topic histories are separate from the legacy per-chat Gist cache. Message merging
also includes thread_id. No Gist toggle, model setting, deployment, or Hub
extraction behavior is changed. Uses existing MEMORY_HUB_URL, MEMORY_HUB_SECRET,
AI_ID; no new environment variable is required.

Scope mismatches and wrong private-agent results are rejected. Group turns retain
their originating agent identity. Missing legacy human identity remains unknown.
The current incoming message stays after the recovered context. Restored trigger
messages are deduplicated across agents; live copies win during recovery merges.

## Remaining Hub contract limitations

1. #44 applies the character budget oldest-first within the selected recent set.
   It can omit the newest turns. Hub should budget newest-first, then reverse for
   chronological output. The bot does not pretend that truncation is full recovery.
2. Combined ai_text lacks individual Telegram reply IDs and original delivery
   timestamps. The bot leaves these IDs empty; it never borrows the input ID.
   Full per-delivery reconstruction requires a later Hub schema/API extension.
3. Older rows have no sender metadata; no retroactive identity inference is made.
4. Existing bot capture limits text to 2000 characters per side and Hub persistence
   has its own limit. This restores stored text, not arbitrary unlimited history.
5. Background capture is still best-effort, not a durable delivery outbox.
6. This change separates topic history; it does not redesign Telegram topic
   delivery, model memory recall, proactive scheduling, or cross-chat memory.

## Verification

Automated tests use mocked Hub/model/Telegram calls. They cover cold/warm paths,
scope rejection, alias mapping, unknown identity, dedup, live merge, failure retry,
hard deadline with no thread buildup or late mutation, topic cache/buffer isolation,
capture fields, and restoring context before the model sees a new message.
A partial-send test checks only confirmed text is captured.

After deployment: exchange a distinctive fact, verify capture succeeds, restart
the bot, and ask about it in the same window. Check [WINDOW] restored and verify
the Telegram reply. Repeat in another chat/topic to verify isolation.
No live end-to-end model or Telegram verification has been performed by this PR.

