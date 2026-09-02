import json
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


MAX_BODY_BYTES = 32 * 1024
RATE_WINDOW_SECONDS = 60
RATE_MAX_REQUESTS = 300
GET_CACHE_TTL = 5.0


def _configured_origin():
    return os.getenv("DASHBOARD_ORIGIN", "").strip().rstrip("/")


def _json(handler, status, payload):
    body = json.dumps(payload, default=str).encode("utf-8")
    origin = _configured_origin()
    request_origin = handler.headers.get("Origin", "").strip().rstrip("/")

    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("X-Frame-Options", "DENY")

    # /health is intentionally public. Protected API endpoints only grant
    # CORS to the configured dashboard origin.
    if origin and request_origin == origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Authorization, Content-Type",
    )
    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, PATCH, POST, DELETE, OPTIONS",
    )
    handler.end_headers()

    if status != 204:
        handler.wfile.write(body)


class DashboardHandler(BaseHTTPRequestHandler):
    store = None
    analytics = None
    admins = None
    user_management = None
    _rate_lock = threading.Lock()
    _rate = defaultdict(deque)
    _cache_lock = threading.Lock()
    _cache = {}

    def log_message(self, fmt, *args):
        return

    @classmethod
    def _rate_limited(cls, handler):
        # Render sees the Vercel proxy as the caller. This is intentionally
        # conservative; the dashboard itself is authenticated separately.
        key = handler.client_address[0]
        now = time.monotonic()
        with cls._rate_lock:
            q = cls._rate[key]
            while q and q[0] <= now - RATE_WINDOW_SECONDS:
                q.popleft()
            if len(q) >= RATE_MAX_REQUESTS:
                return True
            q.append(now)
            return False

    def do_OPTIONS(self):
        _json(self, 204, {})

    @classmethod
    def _cached_json(cls, key):
        now = time.monotonic()
        with cls._cache_lock:
            item = cls._cache.get(key)
            if item and item[0] > now:
                return item[1]
            if item:
                cls._cache.pop(key, None)
        return None

    @classmethod
    def _put_cache(cls, key, payload):
        with cls._cache_lock:
            cls._cache[key] = (time.monotonic() + GET_CACHE_TTL, payload)
        return payload

    def _authorized(self):
        expected = os.getenv("DASHBOARD_API_KEY", "").strip()
        supplied = self.headers.get("Authorization", "")
        return bool(expected) and supplied == f"Bearer {expected}"

    def _origin_allowed(self):
        origin = _configured_origin()
        request_origin = self.headers.get("Origin", "").strip().rstrip("/")
        # Vercel's server-side proxy intentionally does not send a browser
        # Origin header. Direct browser calls must match the configured origin.
        return not origin or not request_origin or request_origin == origin

    def _require_permission(self, permission):
        role = self.headers.get("X-Ghostea-Role", "")
        admin_id = self.headers.get("X-Ghostea-Admin-Id", "")
        if self.admins and self.admins.allowed(role, permission):
            try:
                current = self.store._run_async(
                    self.admins.authorize(admin_id, role)
                )
            except Exception:
                current = None
            if current:
                return True
        _json(self, 403, {"error": "permission_denied"})
        return False

    def _require_read(self):
        return self._require_permission("read")

    def _require_moderate(self):
        return self._require_permission("moderate")

    def _protected(self):
        if self._rate_limited(self):
            _json(self, 429, {"error": "rate_limited"})
            return False
        if not self._origin_allowed():
            _json(self, 403, {"error": "origin_not_allowed"})
            return False
        if not self._authorized():
            _json(self, 401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            return _json(self, 200, {
                "ok": True,
                "service": "ghostea",
                "status": "running",
            })

        if not self._protected():
            return

        try:
            # Authentication/identity is delegated from the Vercel session proxy.
            if path == "/api/auth/me":
                role = self.headers.get("X-Ghostea-Role", "")
                admin_id = self.headers.get("X-Ghostea-Admin-Id", "")
                username = self.headers.get("X-Ghostea-Username", "")
                return _json(self, 200, {
                    "authenticated": True,
                    "admin": {
                        "id": int(admin_id) if admin_id.isdigit() else 0,
                        "username": username,
                        "role": role,
                    },
                })

            if path == "/api/auth/admins":
                if not self._require_permission("manage_admins"):
                    return
                rows = self.store._run_async(self.admins.list_admins())
                return _json(self, 200, {"admins": rows})

            if path == "/api/health":
                # This is a real database check rather than a hard-coded flag.
                self.store._call_sync(
                    self.store.db.select,
                    "ghostea_group_settings",
                    {"select": "chat_id", "limit": "1"},
                )
                return _json(self, 200, {
                    "ok": True,
                    "service": "ghostea",
                    "database": True,
                })

            if path == "/api/groups":
                if not self._require_read(): return
                cache_key = f"groups:{self.headers.get('X-Ghostea-Admin-Id','')}"
                cached = self._cached_json(cache_key)
                if cached is not None:
                    return _json(self, 200, cached)
                rows = self.store._call_sync(
                    self.store.db.select,
                    "ghostea_group_settings",
                    {"select": "*", "order": "updated_at.desc", "limit": "200"},
                )
                return _json(self, 200, self._put_cache(cache_key, {"groups": rows}))

            if path.startswith("/api/groups/") and path.endswith("/analytics"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 5:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                days = int(parse_qs(parsed.query).get("days", ["7"])[0])
                days = max(1, min(days, 90))
                report = self.store._run_async(
                    self.analytics.report(chat_id, days)
                )
                return _json(self, 200, report)

            if path.startswith("/api/groups/") and path.endswith("/risk"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 5:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                days = int(parse_qs(parsed.query).get("days", ["7"])[0])
                days = max(1, min(days, 90))
                if self.risk is None:
                    return _json(self, 503, {"error": "risk_unavailable"})
                cache_key = f"risk:{chat_id}:{days}"
                cached = self._cached_json(cache_key)
                if cached is not None:
                    return _json(self, 200, cached)
                try:
                    report = self.store._run_async(
                        self.risk.report(chat_id, days=days, limit=50)
                    )
                except RuntimeError as error:
                    if str(error) == "risk_data_unavailable":
                        return _json(self, 503, {"error": "risk_data_unavailable"})
                    raise
                return _json(self, 200, self._put_cache(cache_key, report))

            if path.startswith("/api/groups/") and path.endswith("/users"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 5:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                limit = max(1, min(limit, 500))
                cache_key = f"users:{chat_id}:{limit}"
                cached = self._cached_json(cache_key)
                if cached is not None:
                    return _json(self, 200, cached)
                users = self.store._run_async(
                    self.store.get_user_directory(chat_id, limit)
                )
                return _json(self, 200, self._put_cache(cache_key, {"users": users}))

            if path.startswith("/api/groups/") and "/users/" in path and path.endswith("/profile"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 6:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                user_id = int(parts[5])
                profile = self.user_management._run_profile(chat_id, user_id)
                if self.risk is not None:
                    profile["risk"] = self.store._run_async(
                        self.risk.user(chat_id, user_id, days=30)
                    )
                return _json(self, 200, profile)

            if path.startswith("/api/groups/") and path.endswith("/settings"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 5:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                settings = self.store._call_sync(
                    self.store.db.select,
                    "ghostea_group_settings",
                    {"chat_id": f"eq.{chat_id}", "limit": "1"},
                )
                return _json(self, 200, settings[0] if settings else {})

            if path.startswith("/api/groups/") and path.endswith("/logs"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 5:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                limit = max(1, min(limit, 200))
                logs = self.store._call_sync(
                    self.store.db.select,
                    "ghostea_moderation_logs",
                    {"chat_id": f"eq.{chat_id}", "order": "created_at.desc", "limit": str(limit)},
                )
                return _json(self, 200, {"logs": logs})

            if path.startswith("/api/groups/") and path.endswith("/filters"):
                if not self._require_read(): return
                parts = path.split("/")
                if len(parts) != 5:
                    return _json(self, 404, {"error": "not_found"})
                chat_id = int(parts[3])
                filters = self.store._call_sync(
                    self.store.db.select,
                    "ghostea_custom_filters",
                    {"chat_id": f"eq.{chat_id}", "order": "created_at.asc", "limit": "500"},
                )
                return _json(self, 200, {"filters": filters})
        except (ValueError, TypeError):
            return _json(self, 400, {"error": "invalid_request"})
        except Exception:
            # Do not leak Supabase/network internals through the public API.
            return _json(self, 500, {"error": "internal_server_error"})

        return _json(self, 404, {"error": "not_found"})

    def do_POST(self):
        parsed = urlparse(self.path)

        # Vercel authenticates against this server-to-server endpoint.
        if parsed.path == "/api/auth/login":
            if self._rate_limited(self):
                return _json(self, 429, {"error": "rate_limited"})
            if not self._authorized() or not self._origin_allowed():
                return _json(self, 401, {"error": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    return _json(self, 413, {"error": "payload_too_large"})
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    return _json(self, 400, {"error": "object_required"})
                admin = self.admins and self.store._run_async(
                    self.admins.authenticate(
                        payload.get("username"), payload.get("password")
                    )
                )
                if not admin:
                    return _json(self, 401, {"error": "invalid_credentials"})
                return _json(self, 200, {"ok": True, "admin": admin})
            except json.JSONDecodeError:
                return _json(self, 400, {"error": "invalid_json"})
            except Exception:
                return _json(self, 500, {"error": "internal_server_error"})

        if not self._protected():
            return
        parts = parsed.path.split("/")
        if parsed.path == "/api/auth/admins":
            if not self._require_permission("manage_admins"):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    return _json(self, 413, {"error": "payload_too_large"})
                payload = json.loads(self.rfile.read(length) or b"{}")
                admin = self.store._run_async(
                    self.admins.create(
                        payload.get("username"),
                        payload.get("password"),
                        payload.get("role"),
                        payload.get("display_name", ""),
                    )
                )
                return _json(self, 201, {"admin": admin})
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            except json.JSONDecodeError:
                return _json(self, 400, {"error": "invalid_json"})
            except Exception:
                return _json(self, 500, {"error": "internal_server_error"})

        if len(parts) == 5 and parts[1] == "api" and parts[2] == "groups" and parts[4] == "users":
            if not self._require_permission("moderate"):
                return
            try:
                chat_id = int(parts[3])
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_BODY_BYTES:
                    return _json(self, 413, {"error": "payload_too_large"})
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    return _json(self, 415, {"error": "json_required"})
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    return _json(self, 400, {"error": "object_required"})

                target_user_id = int(payload.get("user_id"))
                action = str(payload.get("action", "")).lower()
                reason = str(payload.get("reason", "Dashboard action")).strip()[:500]
                minutes = int(payload.get("minutes", 10))

                if target_user_id == 0:
                    return _json(self, 400, {"error": "invalid_user_id"})
                if action not in {
                    "warn", "unwarn", "reset_warnings",
                    "ban", "unban", "mute", "unmute",
                }:
                    return _json(self, 400, {"error": "invalid_action"})
                if action == "mute" and not 1 <= minutes <= 10080:
                    return _json(self, 400, {"error": "invalid_minutes"})

                # The Vercel proxy authenticates the dashboard. The Telegram
                # bot itself remains the executor of moderation actions.
                if action == "warn":
                    result = self.user_management._run_action(
                        "warn", chat_id, target_user_id, reason=reason
                    )
                elif action == "unwarn":
                    result = self.user_management._run_action(
                        "unwarn", chat_id, target_user_id
                    )
                elif action == "reset_warnings":
                    result = self.user_management._run_action(
                        "reset_warnings", chat_id, target_user_id
                    )
                elif action == "ban":
                    result = self.user_management._run_action(
                        "ban", chat_id, target_user_id
                    )
                elif action == "unban":
                    result = self.user_management._run_action(
                        "unban", chat_id, target_user_id
                    )
                elif action == "mute":
                    result = self.user_management._run_action(
                        "mute", chat_id, target_user_id, minutes=minutes
                    )
                else:
                    result = self.user_management._run_action(
                        "unmute", chat_id, target_user_id
                    )
                return _json(self, 200, result)
            except PermissionError:
                return _json(self, 403, {"error": "target_is_admin"})
            except (ValueError, TypeError):
                return _json(self, 400, {"error": "invalid_request"})
            except Exception:
                return _json(self, 500, {"error": "internal_server_error"})

        parts = parsed.path.split("/")
        if len(parts) != 5 or parts[1] != "api" or parts[2] != "groups" or parts[4] != "filters":
            return _json(self, 404, {"error": "not_found"})
        if not self._require_permission("settings"):
            return
        try:
            chat_id = int(parts[3])
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_BYTES:
                return _json(self, 413, {"error": "payload_too_large"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return _json(self, 415, {"error": "json_required"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                return _json(self, 400, {"error": "object_required"})
            filter_type = payload.get("filter_type")
            value = payload.get("value")
            if filter_type not in ("word", "domain", "pattern"):
                return _json(self, 400, {"error": "invalid_filter_type"})
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 500:
                return _json(self, 400, {"error": "invalid_filter_value"})
            result = self.store._call_sync(
                self.store.db.upsert,
                "ghostea_custom_filters",
                {"chat_id": chat_id, "filter_type": filter_type, "value": value.strip(), "enabled": True},
            )
            return _json(self, 200, result)
        except json.JSONDecodeError:
            return _json(self, 400, {"error": "invalid_json"})
        except (ValueError, TypeError):
            return _json(self, 400, {"error": "invalid_request"})
        except Exception:
            return _json(self, 500, {"error": "internal_server_error"})

    def do_DELETE(self):
        if not self._protected():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/auth/admins/"):
            if not self._require_permission("manage_admins"):
                return
            try:
                admin_id = int(parsed.path.split("/")[-1])
                current_admin_id = int(self.headers.get("X-Ghostea-Admin-Id", "0") or 0)
                if admin_id == current_admin_id:
                    return _json(self, 400, {"error": "cannot_delete_current_admin"})
                result = self.store._run_async(self.admins.delete(admin_id))
                return _json(self, 200, result)
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            except Exception:
                return _json(self, 500, {"error": "internal_server_error"})

        parts = parsed.path.split("/")
        if len(parts) != 6 or parts[1] != "api" or parts[2] != "groups" or parts[4] != "filters":
            return _json(self, 404, {"error": "not_found"})
        if not self._require_permission("settings"):
            return
        try:
            chat_id = int(parts[3])
            filter_id = int(parts[5])
            result = self.store._call_sync(
                self.store.db.delete,
                "ghostea_custom_filters",
                {"chat_id": f"eq.{chat_id}", "id": f"eq.{filter_id}"},
            )
            return _json(self, 200, {"ok": True, "deleted": len(result or [])})
        except (ValueError, TypeError):
            return _json(self, 400, {"error": "invalid_request"})
        except Exception:
            return _json(self, 500, {"error": "internal_server_error"})

    def do_PATCH(self):
        if not self._protected():
            return

        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/auth/admins/"):
            if not self._require_permission("manage_admins"):
                return
            try:
                admin_id = int(parsed.path.split("/")[-1])
                current_admin_id = int(self.headers.get("X-Ghostea-Admin-Id", "0") or 0)
                if admin_id == current_admin_id:
                    return _json(self, 400, {"error": "cannot_modify_current_admin"})
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    return _json(self, 413, {"error": "payload_too_large"})
                payload = json.loads(self.rfile.read(length) or b"{}")
                result = self.store._run_async(self.admins.update(admin_id, payload))
                return _json(self, 200, {"admin": result})
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            except json.JSONDecodeError:
                return _json(self, 400, {"error": "invalid_json"})
            except Exception:
                return _json(self, 500, {"error": "internal_server_error"})

        if not (
            parsed.path.startswith("/api/groups/")
            and parsed.path.endswith("/settings")
        ):
            return _json(self, 404, {"error": "not_found"})

        if not self._require_permission("settings"):
            return

        try:
            parts = parsed.path.split("/")
            if len(parts) != 5:
                return _json(self, 404, {"error": "not_found"})
            chat_id = int(parts[3])

            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_BYTES:
                return _json(self, 413, {"error": "payload_too_large"})

            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                return _json(self, 415, {"error": "json_required"})

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

            # Strict validation prevents the dashboard from writing malformed
            # values that could later crash message processing.
            integer_ranges = {
                "max_warnings": (1, 20),
                "mute1_minutes": (1, 10080),
                "mute2_minutes": (1, 10080),
                "flood_window_seconds": (1, 300),
                "flood_message_limit": (2, 100),
                "flood_mute_minutes": (1, 10080),
                "antiraid_join_limit": (2, 1000),
                "antiraid_window_seconds": (5, 3600),
                "antiraid_lock_minutes": (1, 1440),
                "verification_timeout_seconds": (30, 3600),
                "max_message_length": (100, 10000),
                "repeated_message_window_seconds": (5, 3600),
                "repeated_message_limit": (2, 100),
                "mention_spam_limit": (2, 100),
                "warning_decay_days": (1, 3650),
                "cleanup_max_age_days": (1, 3650),
            }
            for key, (low, high) in integer_ranges.items():
                if key in changes:
                    if isinstance(changes[key], bool) or not isinstance(changes[key], int):
                        return _json(self, 400, {"error": f"invalid_{key}"})
                    if not low <= changes[key] <= high:
                        return _json(self, 400, {"error": f"invalid_{key}"})

            for key in (
                "abuse_filter_enabled", "spam_filter_enabled",
                "link_filter_enabled", "flood_protection_enabled",
                "welcome_enabled", "antiraid_enabled",
                "verification_enabled", "warning_decay_enabled",
                "auto_cleanup_enabled",
            ):
                if key in changes and not isinstance(changes[key], bool):
                    return _json(self, 400, {"error": f"invalid_{key}"})

            if "blocked_link_action" in changes and changes["blocked_link_action"] not in ("delete", "warn"):
                return _json(self, 400, {"error": "invalid_blocked_link_action"})

            result = self.store._call_sync(
                self.store.db.update,
                "ghostea_group_settings",
                changes,
                {"chat_id": f"eq.{chat_id}"},
            )
            return _json(self, 200, result)
        except json.JSONDecodeError:
            return _json(self, 400, {"error": "invalid_json"})
        except (ValueError, TypeError):
            return _json(self, 400, {"error": "invalid_request"})
        except Exception:
            return _json(self, 500, {"error": "internal_server_error"})


def start_web_server(store, analytics, bot=None, risk=None, admins=None):
    port = int(os.getenv("PORT", "10000"))
    DashboardHandler.store = store
    DashboardHandler.analytics = analytics
    DashboardHandler.risk = risk
    DashboardHandler.admins = admins
    DashboardHandler.user_management = None

    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)

    if not hasattr(store, "_call_sync"):
        def _call_sync(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        def _run_async(coro):
            import asyncio
            return asyncio.run(coro)

        store._call_sync = _call_sync
        store._run_async = _run_async


    def run_async(coro):
        import asyncio
        return asyncio.run(coro)

    if bot is not None:
        from ghostea.services.user_management_service import UserManagementService

        manager = UserManagementService(store, bot)

        async def _profile(chat_id, user_id):
            return await manager.profile(chat_id, user_id)

        async def _action(action, chat_id, user_id, **kwargs):
            # Dashboard authentication is already enforced at this API layer.
            # The Telegram bot is the actor, so the audit trail records 0.
            admin_id = 0
            if action == "warn":
                return await manager.warn(chat_id, user_id, admin_id, kwargs.get("reason", "Dashboard action"))
            if action == "unwarn":
                return await manager.remove_warning(chat_id, user_id, admin_id)
            if action == "reset_warnings":
                return await manager.reset_warnings(chat_id, user_id, admin_id)
            if action == "ban":
                return await manager.ban(chat_id, user_id, admin_id)
            if action == "unban":
                return await manager.unban(chat_id, user_id, admin_id)
            if action == "mute":
                return await manager.mute(chat_id, user_id, admin_id, kwargs.get("minutes", 10))
            if action == "unmute":
                return await manager.unmute(chat_id, user_id, admin_id)
            raise ValueError("invalid_action")

        manager._run_profile = lambda chat_id, user_id: run_async(_profile(chat_id, user_id))
        manager._run_action = lambda action, chat_id, user_id, **kwargs: run_async(
            _action(action, chat_id, user_id, **kwargs)
        )
        DashboardHandler.user_management = manager

    thread = threading.Thread(
        target=server.serve_forever,
        name="ghostea-web",
        daemon=True,
    )
    thread.start()
    return server
