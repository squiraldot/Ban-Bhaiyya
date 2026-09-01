# Ghostea Admin Dashboard — Phase 8

A mobile-responsive Vercel dashboard backed by the Render Ghostea API.

## Vercel environment variables

- `GHOSTEA_API_URL` — Render service URL
- `GHOSTEA_API_KEY` — same value as Render `DASHBOARD_API_KEY`
- `GHOSTEA_ADMIN_PASSWORD` — dashboard login password
- `GHOSTEA_SESSION_SECRET` — long random session-signing secret

The browser never receives `GHOSTEA_API_KEY` or `GHOSTEA_SESSION_SECRET`.

## Phase 8 features

- Overview and group selection
- Moderation settings editor
- Protection toggles
- Custom filter management
- Analytics
- Moderation logs
- Secure session login
