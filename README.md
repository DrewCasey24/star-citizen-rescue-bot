# Star Citizen Rescue Bot

A Discord-based rescue paging and dispatch system for a Star Citizen game community.

## Version 0 foundation

This initial scaffold provides:

- Python + `discord.py`
- Slash-command support
- `/ping` health check
- Environment-variable configuration
- PostgreSQL-ready database URL configuration
- Railway-compatible start command

## Environment variables

Copy `.env.example` to `.env` for local development. Never commit `.env` or your Discord bot token.

Required:

- `DISCORD_TOKEN` — Discord bot token

Optional:

- `DATABASE_URL` — PostgreSQL connection URL; Railway will provide this when PostgreSQL is attached
- `GUILD_ID` — Discord server ID for faster development command syncing

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Railway

Railway should start the bot with `python bot.py`. The included `Procfile` and `railway.json` provide startup configuration.

## Roadmap

The rescue system will add request forms, Star Citizen location/service selections, private incident channels, responder paging, persistent response buttons, dispatch status, incident logging, and statistics.
