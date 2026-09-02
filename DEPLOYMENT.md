# Ghostea Deployment

## 1. GitHub
Push the complete repository. Never commit `.env`, BOT_TOKEN, SUPABASE_KEY or dashboard secrets.

## 2. Supabase
Create a project and run `database.sql` in SQL Editor.

The bot uses Supabase REST from Render. Set:
- `SUPABASE_URL`
- `SUPABASE_KEY` (server-side secret; preferably the project's service-role/server key)

Do not put the database key in the Vercel frontend.

## 3. Render
Create a Web Service from this GitHub repo.

Build:
`pip install -r requirements.txt`

Start:
`python main.py`

Health:
`/health`

Environment:
`BOT_TOKEN`
`SUPABASE_URL`
`SUPABASE_KEY`
`DASHBOARD_API_KEY`
`DASHBOARD_ORIGIN`

Render supplies `PORT`.

## 4. Telegram
- Disable Group Privacy in BotFather.
- Add Ghostea as admin.
- Grant Delete Messages, Restrict Members and Ban Users.
- Use a group/supergroup for testing.

## 5. Vercel
Deploy the `dashboard/` directory as the Vercel project root.

Set these Vercel Environment Variables:
- `GHOSTEA_API_URL` = Render service URL
- `GHOSTEA_API_KEY` = same secret as Render `DASHBOARD_API_KEY`
- `GHOSTEA_ADMIN_PASSWORD` = separate dashboard login password
- `GHOSTEA_SESSION_SECRET` = long random secret

The dashboard uses `/api/ghostea` as a server-side proxy, so the Render
API key is never placed in browser JavaScript.

Set Render `DASHBOARD_ORIGIN` to the exact Vercel dashboard origin, for example:
`https://ghostea.vercel.app`

## 6. UptimeRobot
Monitor:
`https://YOUR-RENDER-SERVICE.onrender.com/health`

UptimeRobot checks availability; Render remains the actual host/process.


## Phase 7 security model

The Vercel dashboard no longer sends the Render API key from browser JavaScript.

Vercel server-side environment variables:
- `GHOSTEA_API_URL`
- `GHOSTEA_API_KEY`
- `GHOSTEA_ADMIN_PASSWORD`
- `GHOSTEA_SESSION_SECRET`

The browser authenticates to the Vercel dashboard with the admin password.
Vercel creates an HttpOnly, Secure, SameSite session cookie and proxies only
the allowlisted Ghostea API endpoints. The Render API key remains server-side.

Render environment variables:
- `BOT_TOKEN`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `DASHBOARD_API_KEY`
- `DASHBOARD_ORIGIN`

Rotate any secret that has ever been committed to Git or shared publicly.


### Phase 10
Run the updated `database.sql` once in Supabase to create `ghostea_user_admin_actions`. No new environment variables are required.


## Phase 14 setup

1. Run the new Phase 14 section in `database.sql` in Supabase.
2. Render environment:
   - `GHOSTEA_SUPERADMIN_USERNAME` (optional; defaults to `superadmin`)
   - keep `GHOSTEA_ADMIN_PASSWORD` as the initial Super Admin password.
3. Vercel environment stays:
   - `GHOSTEA_API_URL`
   - `GHOSTEA_API_KEY`
   - `GHOSTEA_SESSION_SECRET`
4. Login with the Super Admin username/password once. Ghostea creates the
   database-backed Super Admin using a scrypt password hash.
5. Create other dashboard admins from **👑 Admins**. Only Super Admin can
   manage dashboard admins.
6. Roles: `super_admin`, `admin`, `moderator`, `viewer`.
7. Server-side RBAC is enforced on Render; dashboard UI hiding is only a
   convenience and is not the security boundary.
