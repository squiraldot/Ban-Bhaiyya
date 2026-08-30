import asyncio
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _json(handler, status, payload):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", os.getenv("DASHBOARD_ORIGIN", "*"))
    handler.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, PATCH, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


class DashboardHandler(BaseHTTPRequestHandler):
    store = None
    analytics = None
    bot = None

    def log_message(self, fmt, *args):
        return

    def do_OPTIONS(self):
        _json(self, 204, {})

    def _authorized(self):
        expected = os.getenv("DASHBOARD_API_KEY", "")
        if not expected:
            return False
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {expected}"

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            return _json(self, 200, {
                "ok": True,
                "service": "banbhai",
                "bot": "polling",
            })

        if path == "/api/health":
            if not self._authorized():
                return _json(self, 401, {"error": "unauthorized"})
            try:
                me = asyncio.run(self.bot.get_me())
                return _json(self, 200, {
                    "ok": True,
                    "telegram": True,
                    "bot_username": me.username,
                    "database": True,
                })
            except Exception as exc:
                return _json(self, 503, {"ok": False, "error": str(exc)})

        if not self._authorized():
            return _json(self, 401, {"error": "unauthorized"})

        if path == "/api/groups":
            try:
                rows = asyncio.run(self.store._call(
                    self.store.db.select,
                    "banbhai_group_settings",
                    {"select": "*", "order": "updated_at.desc", "limit": "200"},
                ))
                return _json(self, 200, {"groups": rows})
            except Exception as exc:
                return _json(self, 500, {"error": str(exc)})

        match = re.match(r"^/api/groups/(-?\d+)/settings$", path)
        if match:
            chat_id = int(match.group(1))
            try:
                data = asyncio.run(self.store.get_settings(chat_id))
                return _json(self, 200, data)
            except Exception as exc:
                return _json(self, 500, {"error": str(exc)})

        match = re.match(r"^/api/groups/(-?\d+)/analytics$", path)
        if match:
            chat_id = int(match.group(1))
            days = parse_qs(urlparse(self.path).query).get("days", ["7"])[0]
            try:
                report = asyncio.run(self.analytics.report(chat_id, int(days)))
                return _json(self, 200, report)
            except Exception as exc:
                return _json(self, 500, {"error": str(exc)})

        return _json(self, 404, {"error": "not_found"})

    def do_PATCH(self):
        if not self._authorized():
            return _json(self, 401, {"error": "unauthorized"})

        path = urlparse(self.path).path
        match = re.match(r"^/api/groups/(-?\d+)/settings$", path)
        if not match:
            return _json(self, 404, {"error": "not_found"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 32768:
                return _json(self, 413, {"error": "payload_too_large"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                return _json(self, 400, {"error": "object_required"})

            # Dashboard may only change known, safe settings.
            allowed = {
                "max_warnings", "mute1_minutes", "mute2_minutes",
                "flood_window_seconds", "flood_message_limit",
                "flood_mute_minutes", "blocked_link_action",
                "abuse_filter_enabled", "spam_filter_enabled",
                "link_filter_enabled", "flood_protection_enabled",
                "welcome_enabled", "antiraid_enabled",
                "antiraid_join_limit", "antiraid_window_seconds",
                "antiraid_lock_minutes", "verification_enabled",
                "verification_timeout_seconds", "max_message_length",
                "repeated_message_window_seconds", "repeated_message_limit",
                "mention_spam_limit", "warning_decay_enabled",
                "warning_decay_days", "auto_cleanup_enabled",
                "cleanup_max_age_days",
            }
            changes = {k: v for k, v in payload.items() if k in allowed}
            if not changes:
                return _json(self, 400, {"error": "no_supported_settings"})

            chat_id = int(match.group(1))
            result = asyncio.run(self.store.update_settings(chat_id, changes))
            return _json(self, 200, result)
        except Exception as exc:
            return _json(self, 400, {"error": str(exc)})


def start_web_server(store, analytics, bot):
    port = int(os.getenv("PORT", "10000"))
    DashboardHandler.store = store
    DashboardHandler.analytics = analytics
    DashboardHandler.bot = bot

    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="banbhai-web",
        daemon=True,
    )
    thread.start()
    return server
