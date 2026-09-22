# Cross-window context

Cross-window context is separate from current-window history recovery.

- Read live in-process histories immediately, including listening-only messages.
- Refill a separate cache from the existing Hub POST /api/window/context endpoint.
- One background worker, up to four windows per refresh, at most once per minute
  per window. Requests use connect/read timeouts; replies never join this worker.
- Use only the latest 48 hours, six events per window, four windows and 6000
  characters in total. Preserve speaker, source chat/topic and event time.
- Owner DM (CECI_ID) and PRIVATE_CHATS can receive owner-private and group context.
  Public groups receive public-group context only. Other humans' DMs are excluded
  as both sources and destinations.
- Known sources come from TG_CHAT_ID, PRIVATE_CHATS, PROACTIVE_CHAT_IDS, CECI_ID
  and observed local windows. This is not a Hub-wide conversation scan.
- After a cold start, the first reply may precede the background refill. Unknown
  topic IDs cannot be rediscovered by this endpoint until observed again.
- No new environment variables, model calls, Gist reads or Hub changes.
  Do not enable Gist I/O to enable this feature.

Validation: mention a distinctive harmless topic in a group, then ask about it
in the owner's DM. Check [CROSS-WINDOW] restored logs after a restart. Also
verify a private-only topic is absent from public-group cross-window input.
