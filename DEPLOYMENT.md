# Deployment State

Last updated: 2026-07-30

## Deployment target

- Host: Render
- Service ID: `srv-d806qq3rjlhs73a2cl50`
- Repository: `cirel94june/cloudy-telegram-bot`
- No Fly.io copy is configured for Cloudy

Runtime health can change with Render free-tier availability. Verify the Render dashboard before treating the service as live.

## Safety rules

1. Exactly one deployment may own this Telegram bot token at a time.
2. Do not copy Jasper's `fly.toml` or Fly webhook startup behavior into this repository unless Cloudy is intentionally migrated.
3. The inactive-owner mention notification was removed on 2026-07-30. Do not restore it without an explicit request.
4. Jasper, Lucien, and Cloudy share an architecture, but changes must be reviewed and deployed per repository.
