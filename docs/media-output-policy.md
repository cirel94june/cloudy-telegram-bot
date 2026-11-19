# Telegram reply policy (2026-11-19)

- Incoming voice and audio messages are ignored without downloading, transcribing, or replying. Already queued voice jobs are also ignored.
- For group photos and albums shared by people other than the configured owner, the model may reply when it has something concrete to say, or return `[SKIP]`. The marker is consumed before Telegram delivery and is not saved as an assistant reply. Owner photos and private chats keep their normal reply behavior.
- Primary and backup API routes and the follow-up call after borrowing tools receive the same image policy.
- Relay reasoning blocks, including `<antml thinking>` and `<antml:thinking>`, are separated before capability processing. Nested and unclosed blocks are hidden; an orphan closing tag discards the uncertain prefix. The send guards and AI history reads also apply this cleanup.
- Existing opt-in reasoning buttons retain their behavior. This fixes reasoning mixed into normal messages. Existing Telegram messages and stored raw records are not deleted or rewritten.

Validation: `python -m unittest discover -s tests`. Tests use synthetic payloads and mocked network calls; they do not send Telegram messages or invoke real models. Coverage includes webhook voice/audio handling, queued voice jobs, optional image prompts in both API formats, single images/albums, skip delivery, owner behavior, relay tags, and history reuse.

Whether a real model finds a particular image interesting remains a model decision; local tests verify the prompt and delivery behavior.
