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
The `dashboard/` directory is a static admin UI. Deploy that directory as a Vercel project.
Enter the Render API URL and `DASHBOARD_API_KEY` in the dashboard. Do not hardcode secrets.

For a production-grade public dashboard, put authentication in a Vercel server-side proxy rather than relying only on browser-entered API credentials.

## 6. UptimeRobot
Monitor:
`https://YOUR-RENDER-SERVICE.onrender.com/health`

UptimeRobot checks availability; Render remains the actual host/process.
