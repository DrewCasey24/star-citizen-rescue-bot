# Star Citizen Rescue Bot

A Discord-based rescue paging and dispatch system for Star Citizen communities.

## Current features

- Permanent Request Assistance workflow
- Private numbered rescue incident channels
- Service-specific responder paging
- Cross-sector responder controls
- Safeguarded P1/P2/P3 priority system
- PostgreSQL incident persistence
- Live dispatch board
- Rescue history, completed-incident log, and statistics
- Discord-authenticated web operations dashboard
- Database-backed responder-role, service-paging, channel, and incident-category configuration
- Multi-guild operation with independent settings and incident data per Discord server

## Multi-guild architecture

The production bot no longer depends on a fixed `GUILD_ID` environment variable. Application commands are synced globally, so the same bot installation can serve any Discord server where the application is installed.

Every server is isolated by its Discord guild ID in PostgreSQL. Incident numbering counters, incidents, dispatch-board configuration, rescue-log configuration, responder roles, paging roles, request channels, and incident categories are all stored per guild.

Installing the bot into another Discord server therefore does not require editing Railway variables or redeploying with a different server ID. Configure each server independently from the web dashboard after installation.

Global Discord application-command updates can take longer to appear than development-only guild command syncs, but this avoids limiting production commands to one server.

## Environment variables

Copy `.env.example` to `.env` for local development. Never commit `.env`, Discord bot credentials, OAuth client secrets, database passwords, or session secrets.

Bot service:

- `DISCORD_TOKEN` — Discord bot token
- `DATABASE_URL` — PostgreSQL connection URL

Dashboard service:

- `DISCORD_TOKEN` — same bot token used by the Discord bot
- `DATABASE_URL` — same PostgreSQL database used by the Discord bot
- `DISCORD_CLIENT_ID` — Discord application client ID
- `DISCORD_CLIENT_SECRET` — Discord application OAuth2 client secret
- `DISCORD_REDIRECT_URI` — public dashboard URL followed by `/oauth/callback`
- `DASHBOARD_SESSION_SECRET` — long random value used to sign dashboard sessions
- `DASHBOARD_COOKIE_SECURE=true` — keep enabled in production HTTPS

## Bot startup

The bot starts through the configuration wrapper so dashboard role/category changes are picked up automatically:

```bash
python run_bot.py
```

The bot refreshes database-backed responder-role and active-incident-category configuration approximately every 10 seconds. Existing hard-coded sector role names remain fallback defaults until a server saves dashboard configuration.

## Web dashboard

Run locally with:

```bash
uvicorn dashboard:app --host 0.0.0.0 --port 8000
```

The dashboard uses Discord OAuth2 scopes `identify guilds`. Only servers where the signed-in user has **Manage Server** permission can be configured.

Dashboard sections include:

- Overview metrics
- Active incidents
- Recent rescue history
- Responder roles allowed to use incident controls
- Per-service paging roles
- Request Assistance channel
- Live Dispatch Board channel
- Completed Rescue Log channel
- Active Incident category

Changing the Request Assistance channel posts a fresh request panel in the selected channel. Changing the Dispatch Board channel posts and tracks a fresh board there. Rescue Log changes update the existing database-backed log target. Old messages are not automatically deleted.

## Railway

Use two Railway services pointing to this same repository and the same PostgreSQL service.

Bot service start command:

```bash
python run_bot.py
```

Dashboard service start command:

```bash
uvicorn dashboard:app --host 0.0.0.0 --port $PORT
```

Give the dashboard service a public Railway domain, then use that HTTPS domain plus `/oauth/callback` as the Discord OAuth2 redirect URI. The repository also contains `railway.dashboard.json` as a reference dashboard deployment configuration.

## Security

The dashboard does not keep the Discord OAuth access token in the browser session. It stores only the signed-in user's basic identity and server permission snapshot. Configuration writes include a CSRF token and are restricted to users who authenticated with Manage Server permission.
