import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _json(handler, status, payload):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    origin = os.getenv("DASHBOARD_ORIGIN", "").strip()
    handler.send_header("Access-Control-Allow-Origin", origin or "*")
    handler.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, PATCH, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


class DashboardHandler(BaseHTTPRequestHandler):
    store = None
    analytics = None

    def log_message(self, fmt, *args):
        return

    def do_OPTIONS(self):
        _json(self, 204, {})

    def _authorized(self):
        expected = os.getenv("DASHBOARD_API_KEY", "").strip()
        if not expected:
            return False
        return self.headers.get("Authorization", "") == f"Bearer {expected}"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            return _json(self, 200, {
                "ok": True,
                "service": "ghostea",
                "status": "running",
            })

        if not self._authorized():
            return _json(self, 401, {"error": "unauthorized"})

        try:
            if path == "/api/health":
                return _json(self, 200, {
                    "ok": True,
                    "service": "ghostea",
                    "database": True,
                })

            if path == "/api/groups":
                rows = self.store._call_sync(
                    self.store.db.select,
                    "ghostea_group_settings",
                    {"select": "*", "order": "updated_at.desc", "limit": "200"},
                )
                return _json(self, 200, {"groups": rows})

            if path.startswith("/api/groups/") and path.endswith("/analytics"):
                parts = path.split("/")
                chat_id = int(parts[3])
                days = int(parse_qs(parsed.query).get("days", ["7"])[0])
                # AnalyticsService is async; use the dedicated sync helper.
                report = self.store._run_async(
                    self.analytics.report(chat_id, days)
                )
                return _json(self, 200, report)

            if path.startswith("/api/groups/") and path.endswith("/settings"):
                parts = path.split("/")
                chat_id = int(parts[3])
                settings = self.store._call_sync(
                    self.store.db.select,
                    "ghostea_group_settings",
                    {"chat_id": f"eq.{chat_id}", "limit": "1"},
                )
                return _json(self, 200, settings[0] if settings else {})
        except Exception as exc:
            return _json(self, 500, {"error": str(exc)})

        return _json(self, 404, {"error": "not_found"})

    def do_PATCH(self):
        if not self._authorized():
            return _json(self, 401, {"error": "unauthorized"})

        parsed = urlparse(self.path)
        if not (parsed.path.startswith("/api/groups/") and parsed.path.endswith("/settings")):
            return _json(self, 404, {"error": "not_found"})

        try:
            chat_id = int(parsed.path.split("/")[3])
            length = int(self.headers.get("Content-Length", "0"))
            if length > 32768:
                return _json(self, 413, {"error": "payload_too_large"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                return _json(self, 400, {"error": "object_required"})

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

            result = self.store._call_sync(
                self.store.db.update,
                "ghostea_group_settings",
                changes,
                {"chat_id": f"eq.{chat_id}"},
            )
            return _json(self, 200, result)
        except Exception as exc:
            return _json(self, 400, {"error": str(exc)})


def start_web_server(store, analytics):
    port = int(os.getenv("PORT", "10000"))
    DashboardHandler.store = store
    DashboardHandler.analytics = analytics

    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)

    # Phase3Store methods are async wrappers around synchronous REST calls.
    # These helpers keep the dashboard thread independent of the Telegram loop.
    if not hasattr(store, "_call_sync"):
        import asyncio

        def _call_sync(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        def _run_async(coro):
            return asyncio.run(coro)

        store._call_sync = _call_sync
        store._run_async = _run_async

    thread = threading.Thread(
        target=server.serve_forever,
        name="ghostea-web",
        daemon=True,
    )
    thread.start()
    return server
