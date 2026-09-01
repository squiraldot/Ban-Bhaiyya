# Ghostea 👻

Production-ready Telegram moderation bot built with Python and `python-telegram-bot`.

## Architecture

```text
GitHub → Render (Ghostea bot + /health + admin API)
             ↓
          Supabase
             ↑
Vercel → Admin Dashboard
             ↑
        UptimeRobot
```

## Local Android/Termux test

```bash
pip install -r requirements.txt

export BOT_TOKEN="YOUR_BOT_TOKEN"
export SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
export SUPABASE_KEY="YOUR_SERVER_SIDE_SUPABASE_KEY"
export DASHBOARD_API_KEY="YOUR_RANDOM_LONG_SECRET"
export DASHBOARD_ORIGIN="http://localhost:3000"

python main.py
```

Do not commit `.env`, bot tokens, Supabase server keys, or `data/warnings.json`.

## Telegram permissions

Add Ghostea as an administrator with:

- Delete Messages
- Restrict Members
- Ban Users

Disable Group Privacy in BotFather.

## Supabase

Run `database.sql` once in Supabase SQL Editor.

## Render

Use:

```text
Build: pip install -r requirements.txt
Start: python main.py
Health: /health
```

Render supplies `PORT` automatically.

Required environment variables:

```text
BOT_TOKEN
SUPABASE_URL
SUPABASE_KEY
DASHBOARD_API_KEY
DASHBOARD_ORIGIN
```

## UptimeRobot

Monitor:

```text
https://YOUR-RENDER-SERVICE.onrender.com/health
```

UptimeRobot monitors the service; it is not the process that hosts the bot.

## Main moderation flow

- abusive message → delete + warning
- warning 1 → configured mute (default 2 min)
- warning 2 → configured mute (default 5 min)
- warning 3 → real permanent Telegram ban
- admins/owners are excluded from automatic moderation

## Filter files

```text
ghostea/filters/abusive_words.txt
ghostea/filters/spam_patterns.txt
ghostea/filters/blocked_domains.txt
```

One entry per line. Use `/reloadfilters` after editing files on a running instance.

## Phase 4–6

Includes welcome, anti-raid, group settings, verification gate, repeated-message/mention protection, reputation, analytics, CSV/JSON export, health API, and persistent moderation data.

## Important

The Vercel dashboard should never receive `SUPABASE_KEY`. It should call Ghostea's authenticated API using the dashboard API secret through a secure server-side/proxy setup.


## Phase 7 — Production hardening

- Secure Vercel server-side dashboard proxy
- HttpOnly/Secure/SameSite dashboard session
- Render API authentication + origin checks
- API rate limiting and payload limits
- Strict dashboard setting validation
- Generic API errors (no internal exception leakage)
- Warning increment serialization within the bot process
- Clean shutdown of the Render web server
- No browser exposure of `DASHBOARD_API_KEY`

## Phase 8 — Full Admin Control Center

The Vercel dashboard now provides a protected admin control center for configured groups.

Features:
- Group selection and live status
- Group moderation settings and protection switches
- Custom word/domain/pattern filters
- Analytics with warning/action breakdowns
- Recent moderation logs
- Mobile-responsive UI
- Secure Vercel server-side proxy; Render and Supabase secrets remain server-side

No new database migration is required for Phase 8; it uses the existing settings,
custom-filter, analytics and moderation-log tables.
