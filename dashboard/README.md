# Ghostea Admin Dashboard

This dashboard uses a Vercel server-side proxy.

Set these Vercel Environment Variables:

- `GHOSTEA_API_URL` — Render service URL, e.g. `https://ghostea.onrender.com`
- `GHOSTEA_API_KEY` — same value as Render `DASHBOARD_API_KEY`
- `GHOSTEA_ADMIN_PASSWORD` — a separate dashboard login password
- `GHOSTEA_SESSION_SECRET` — long random secret used to sign sessions

The Render API key and session secret are server-side only; they are not sent
to the browser.

Deploy this directory as the Vercel project root.
