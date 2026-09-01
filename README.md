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

## Phase 9 — Centralized Moderation Engine

Phase 9 routes message-content checks through one moderation decision layer.
It evaluates:

- abusive language
- spam patterns
- blocked links
- mention spam
- repeated messages
- excessive message length

Each detection receives a risk score and a deterministic priority. The score
is recorded in moderation logs/announcements; it does not bypass the normal
warning limit. Administrators remain exempt from automatic moderation.

Flood protection remains a separate action because it is based on message
frequency rather than message content.


## Phase 10 — User Management

The admin dashboard now includes a **Users** section.

Supported actions:

```text
Load User
Warn
Remove Warning
Reset Warnings
Mute
Unmute
Ban
Unban
```

A user profile includes:

- Telegram account/status when accessible
- Current warning count
- Reputation score
- Warning history
- Moderation history

Dashboard moderation actions are executed by the Telegram bot and audited in
`ghostea_user_admin_actions`.

Run the updated `database.sql` once in Supabase before using the new audit
table.

For safety, the bot refuses dashboard/automatic ban, mute, or warning actions
against Telegram administrators/owners.


## Phase 11 — Persistent Security & Recovery

Phase 11 hardens the two time-based security systems that previously depended
only on in-memory/runtime state.

### Anti-Raid recovery
- Anti-Raid now snapshots the group's original default permissions.
- The temporary lock is stored in `ghostea_security_locks`.
- The lock is automatically restored after its configured duration.
- Pending locks are recovered after a Render restart.
- The old `set_permissions(..., until_date=...)` approach was removed because
  `set_permissions` changes default chat permissions and does not support an
  `until_date`; timed member restrictions use `restrict_member` instead.

### Verification expiry enforcement
- Unverified members no longer simply become unrestricted when the verification
  timer expires.
- A background security worker checks expired verification records.
- Expired, still-present non-admin members are banned.
- Expired verification records are cleaned up.
- Verification records are removed immediately after successful verification.
- Pending verification expiry is recovered after a Render restart.

### Database
Run the updated `database.sql` once in Supabase. It adds:

```text
ghostea_security_locks
```

No new environment variables are required.


## Phase 12 — Moderation Risk Center

Phase 12 adds a read-only moderation intelligence layer to the dashboard.

- Risk Center shows the most active/high-risk users for a selected period.
- Risk is derived from existing moderation logs and warning history.
- Recent events receive more weight than older events using a seven-day half-life.
- Existing Phase 9 `risk_score` values are respected when present in log details.
- User profiles now include an advisory risk score and level.
- Risk intelligence never directly triggers a ban, mute, warning, or other moderation action.
- No new environment variables or database tables are required.

Risk levels:

```text
0–24   Low
25–49  Medium
50–74  High
75–100 Critical
```

These scores are advisory indicators for moderators, not proof of wrongdoing.
