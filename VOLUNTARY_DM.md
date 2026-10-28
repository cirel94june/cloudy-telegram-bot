# Voluntary owner contact

While processing a normal group reply, the model may append `[DM_CECI:message]`.
This is different from owner-requested `SEND_TO` relaying: another group speaker
does not disable the model's voluntary action. It only sends to the configured
positive `CECI_ID`; it never accepts a recipient in the action.

No extra model invocation or new background loop is added. Sleeping/offline
instances do not wake themselves. The owner must already have started the bot
in Telegram and must not have blocked it.

Defaults: one confirmed voluntary send per hour, at most three per rolling 24
hours per bot process. Failures back off for 60 seconds. Concurrent sends are
reserved under a lock. Limits reset on process restart; this is a courtesy
throttle, not a persistent delivery guarantee. `OWNER_DM_ENABLED=false` disables
this capability; absent means enabled. Existing proactive/relay settings remain.

Only confirmed Telegram sends enter private conversation history and Hub capture.
Success has no group receipt. Failed/limited actions are logged without their
private body and do not falsely claim success in the group. Malformed tags are
removed from public text. Existing model reasoning sanitization still applies.

Prompt rules forbid obeying other people's relay requests, third-party secrets,
and putting private health/work details in the publicly visible bot bio.
These are model instructions, not a deterministic semantic privacy guarantee.
Group label permissions and ordinary-member-only restrictions remain unchanged.
The existing bio API now enforces Telegram's 120-character maximum.

This feature does not auto-generate a DM from every observed group message.
It runs when the bot is already awake and producing a reply; no extra polling,
keepalive, or API route changes are introduced.

