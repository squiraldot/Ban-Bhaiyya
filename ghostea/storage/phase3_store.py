import asyncio
import time
from datetime import datetime, timedelta, timezone

from ghostea.config import (
    DEFAULT_BLOCKED_LINK_ACTION,
    DEFAULT_MAX_WARNINGS,
    DEFAULT_MUTE_MINUTES,
    DEFAULT_SPAM_MESSAGE_LIMIT,
    DEFAULT_SPAM_MUTE_MINUTES,
    DEFAULT_SPAM_WINDOW_SECONDS,
)


class Phase3Store:
    """Persistent settings, warnings, custom filters and moderation logs."""

    def __init__(self, db):
        self.db = db
        self._warning_lock = asyncio.Lock()
        self._settings_cache = {}
        self._filters_cache = {}
        self._directory_touch = {}
        self._reputation_locks = {}
        self._cache_ttl = 10.0
        self._directory_touch_ttl = 60.0

    async def _call(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def invalidate_group_cache(self, chat_id):
        chat_id = int(chat_id)
        self._settings_cache.pop(chat_id, None)
        for key in list(self._filters_cache):
            if key[0] == chat_id:
                self._filters_cache.pop(key, None)

    async def touch_user(self, chat_id, user_id):
        """Keep a lightweight observed-user directory without writing per message."""
        key = (int(chat_id), int(user_id))
        now = time.monotonic()
        last = self._directory_touch.get(key, 0.0)
        if now - last < self._directory_touch_ttl:
            return
        self._directory_touch[key] = now
        if len(self._directory_touch) > 100000:
            cutoff = now - 3600.0
            stale = [k for k, seen in self._directory_touch.items() if seen < cutoff]
            for stale_key in stale[:10000]:
                self._directory_touch.pop(stale_key, None)
        try:
            await self._call(
                self.db.upsert,
                "ghostea_user_directory",
                {
                    "chat_id": int(chat_id),
                    "user_id": int(user_id),
                    "last_activity": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            # Directory indexing must never break moderation.
            self._directory_touch.pop(key, None)

    async def get_settings(self, chat_id):
        now = time.monotonic()
        cached = self._settings_cache.get(int(chat_id))
        if cached and cached[0] > now:
            return dict(cached[1])

        rows = await self._call(
            self.db.select,
            "ghostea_group_settings",
            {"chat_id": f"eq.{chat_id}", "limit": "1"},
        )

        if rows:
            self._settings_cache[int(chat_id)] = (now + self._cache_ttl, dict(rows[0]))
            return rows[0]

        defaults = {
            "chat_id": chat_id,
            "max_warnings": DEFAULT_MAX_WARNINGS,
            "mute1_minutes": DEFAULT_MUTE_MINUTES[1],
            "mute2_minutes": DEFAULT_MUTE_MINUTES[2],
            "flood_window_seconds": DEFAULT_SPAM_WINDOW_SECONDS,
            "flood_message_limit": DEFAULT_SPAM_MESSAGE_LIMIT,
            "flood_mute_minutes": DEFAULT_SPAM_MUTE_MINUTES,
            "blocked_link_action": DEFAULT_BLOCKED_LINK_ACTION,
            "abuse_filter_enabled": True,
            "spam_filter_enabled": True,
            "link_filter_enabled": True,
            "flood_protection_enabled": True,
            "welcome_enabled": True,
            "antiraid_enabled": True,
            "antiraid_join_limit": 8,
            "antiraid_window_seconds": 20,
            "antiraid_lock_minutes": 10,
            "auto_cleanup_enabled": False,
            "verification_enabled": True,
            "verification_timeout_seconds": 120,
            "min_account_age_days": 0,
            "new_member_restriction_minutes": 0,
            "repeated_message_window_seconds": 60,
            "repeated_message_limit": 3,
            "mention_spam_limit": 6,
            "max_message_length": 4000,
            "warning_decay_enabled": True,
            "warning_decay_days": 30,
            "cleanup_max_age_days": 30,
        }

        await self._call(self.db.upsert, "ghostea_group_settings", defaults)
        self._settings_cache[int(chat_id)] = (time.monotonic() + self._cache_ttl, dict(defaults))
        return defaults

    async def update_settings(self, chat_id, changes):
        changes = dict(changes)
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = await self._call(
            self.db.update,
            "ghostea_group_settings",
            changes,
            {"chat_id": f"eq.{chat_id}"},
        )
        self.invalidate_group_cache(chat_id)
        return rows[0] if rows else await self.get_settings(chat_id)

    async def _warning_policy(self, chat_id):
        settings = await self.get_settings(chat_id)
        enabled = bool(settings.get("warning_decay_enabled", True))
        days = max(1, int(settings.get("warning_decay_days", 30)))
        return enabled, days

    async def get_warning_count(self, chat_id, user_id):
        rows = await self._call(
            self.db.select,
            "ghostea_warnings",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        current = rows[0] if rows else None

        decay_enabled, decay_days = await self._warning_policy(chat_id)
        if not decay_enabled:
            return int(current["count"]) if current else 0

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=decay_days)
        cutoff = cutoff_dt.isoformat()
        # Most reads can use the compact count row without touching warning
        # history. Recalculate only when that row has aged past the decay
        # window or does not exist.
        if current:
            updated = self._parse_datetime(current.get("updated_at"))
            if updated and updated >= cutoff_dt:
                return int(current.get("count", 0))

        recent = await self._call(
            self.db.select,
            "ghostea_warning_history",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "active": "eq.true",
                "created_at": f"gte.{cutoff}",
                "select": "id,created_at",
                "order": "created_at.desc",
                "limit": "1000",
            },
        )
        count = len(recent)
        newest = recent[0].get("created_at") if recent else None
        await self._call(
            self.db.upsert,
            "ghostea_warnings",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "count": count,
                # Track the newest active warning, not the recalculation time,
                # so the next individual warning can decay naturally.
                "updated_at": newest or datetime.now(timezone.utc).isoformat(),
            },
        )
        return count

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    async def add_warning(self, chat_id, user_id, reason, source):
        # Serialize increments within this bot process so two simultaneous
        # detections do not overwrite the same warning count.
        async with self._warning_lock:
            count = await self.get_warning_count(chat_id, user_id) + 1

            await self._call(
                self.db.upsert,
                "ghostea_warnings",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "count": count,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self._call(
                self.db.insert,
                "ghostea_warning_history",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "reason": reason,
                    "source": source,
                    "active": True,
                },
            )
            await self.log(
                chat_id, user_id, "WARN", reason,
                f"warning_count={count};source={source}",
            )
            return count

    async def remove_warning(self, chat_id, user_id):
        decay_enabled, decay_days = await self._warning_policy(chat_id)
        query = {
            "chat_id": f"eq.{chat_id}",
            "user_id": f"eq.{user_id}",
            "active": "eq.true",
            "order": "created_at.desc",
            "limit": "1",
        }
        if decay_enabled:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=decay_days)).isoformat()
            query["created_at"] = f"gte.{cutoff}"
        rows = await self._call(self.db.select, "ghostea_warning_history", query)
        if rows:
            await self._call(
                self.db.update, "ghostea_warning_history", {"active": False},
                {"id": f"eq.{rows[0]['id']}"},
            )
        count = await self.get_warning_count(chat_id, user_id)
        await self._call(
            self.db.upsert,
            "ghostea_warnings",
            {"chat_id": chat_id, "user_id": user_id, "count": count, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        return count

    async def reset_warnings(self, chat_id, user_id):
        await self._call(
            self.db.update, "ghostea_warning_history", {"active": False},
            {"chat_id": f"eq.{chat_id}", "user_id": f"eq.{user_id}", "active": "eq.true"},
        )
        await self._call(
            self.db.upsert,
            "ghostea_warnings",
            {"chat_id": chat_id, "user_id": user_id, "count": 0, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
        await self.log(chat_id, user_id, "RESET_WARNINGS", "Admin reset", "")

    async def get_history(self, chat_id, user_id, limit=10):
        return await self._call(
            self.db.select,
            "ghostea_warning_history",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def add_custom_filter(self, chat_id, filter_type, value):
        result = await self._call(
            self.db.upsert,
            "ghostea_custom_filters",
            {
                "chat_id": chat_id,
                "filter_type": filter_type,
                "value": value,
                "enabled": True,
            },
        )
        self.invalidate_group_cache(chat_id)
        return result

    async def remove_custom_filter(self, chat_id, filter_type, value):
        result = await self._call(
            self.db.delete,
            "ghostea_custom_filters",
            {
                "chat_id": f"eq.{chat_id}",
                "filter_type": f"eq.{filter_type}",
                "value": f"eq.{value}",
            },
        )
        self.invalidate_group_cache(chat_id)
        return result

    async def remove_custom_filter_by_id(self, chat_id, filter_id):
        result = await self._call(
            self.db.delete,
            "ghostea_custom_filters",
            {"chat_id": f"eq.{chat_id}", "id": f"eq.{int(filter_id)}"},
        )
        self.invalidate_group_cache(chat_id)
        return result

    async def get_custom_filters(self, chat_id, filter_type=None):
        key = (int(chat_id), filter_type or "*")
        now = time.monotonic()
        cached = self._filters_cache.get(key)
        if cached and cached[0] > now:
            return list(cached[1])

        query = {
            "chat_id": f"eq.{chat_id}",
            "enabled": "eq.true",
            "order": "created_at.asc",
        }
        if filter_type:
            query["filter_type"] = f"eq.{filter_type}"

        rows = await self._call(
            self.db.select,
            "ghostea_custom_filters",
            query,
        )
        self._filters_cache[key] = (time.monotonic() + self._cache_ttl, list(rows))
        return rows

    async def log(self, chat_id, user_id, action, reason="", details=""):
        result = await self._call(
            self.db.insert,
            "ghostea_moderation_logs",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "action": action,
                "reason": reason,
                "details": details,
            },
        )
        return result

    async def recent_logs(self, chat_id, limit=20):
        return await self._call(
            self.db.select,
            "ghostea_moderation_logs",
            {
                "chat_id": f"eq.{chat_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def record_join(self, chat_id, user_id):
        result = await self._call(
            self.db.insert,
            "ghostea_join_events",
            {"chat_id": chat_id, "user_id": user_id},
        )
        await self.touch_user(chat_id, user_id)
        return result

    async def log_raid_event(self, chat_id, action, details=""):
        return await self._call(
            self.db.insert,
            "ghostea_raid_events",
            {"chat_id": chat_id, "action": action, "details": details},
        )

    async def get_stats(self, chat_id):
        try:
            warnings = await self._call(
                self.db.count, "ghostea_warning_history",
                {"chat_id": f"eq.{chat_id}"},
            )
            actions = await self._call(
                self.db.count, "ghostea_moderation_logs",
                {"chat_id": f"eq.{chat_id}"},
            )
            joins = await self._call(
                self.db.count, "ghostea_join_events",
                {"chat_id": f"eq.{chat_id}"},
            )
            return {"warnings": warnings, "actions": actions, "joins": joins}
        except Exception:
            # Fallback for older PostgREST configurations without count support.
            warnings = await self._call(self.db.select, "ghostea_warning_history", {"chat_id": f"eq.{chat_id}", "select": "id", "limit": "1000"})
            actions = await self._call(self.db.select, "ghostea_moderation_logs", {"chat_id": f"eq.{chat_id}", "select": "id", "limit": "1000"})
            joins = await self._call(self.db.select, "ghostea_join_events", {"chat_id": f"eq.{chat_id}", "select": "id", "limit": "1000"})
            return {"warnings": len(warnings), "actions": len(actions), "joins": len(joins), "partial": True}


    async def save_verification(self, chat_id, user_id, token, expires_at):
        return await self._call(
            self.db.upsert,
            "ghostea_verifications",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "token": token,
                "expires_at": expires_at,
                "verified": False,
            },
        )

    async def get_verification(self, chat_id, user_id):
        rows = await self._call(
            self.db.select,
            "ghostea_verifications",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def verify_user(self, chat_id, user_id):
        return await self._call(
            self.db.update,
            "ghostea_verifications",
            {"verified": True},
            {"chat_id": f"eq.{chat_id}", "user_id": f"eq.{user_id}"},
        )

    async def delete_verification(self, chat_id, user_id):
        return await self._call(
            self.db.delete,
            "ghostea_verifications",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
            },
        )


    async def upsert_security_lock(
        self, chat_id, lock_type, expires_at, original_permissions
    ):
        return await self._call(
            self.db.upsert,
            "ghostea_security_locks",
            {
                "chat_id": chat_id,
                "lock_type": lock_type,
                "expires_at": expires_at,
                "original_permissions": original_permissions or {},
            },
        )

    async def get_security_lock(self, chat_id, lock_type):
        rows = await self._call(
            self.db.select,
            "ghostea_security_locks",
            {
                "chat_id": f"eq.{chat_id}",
                "lock_type": f"eq.{lock_type}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def get_active_security_locks(self):
        return await self._call(
            self.db.select,
            "ghostea_security_locks",
            {
                "order": "expires_at.asc",
                "limit": "200",
            },
        )

    async def delete_security_lock(self, chat_id, lock_type):
        return await self._call(
            self.db.delete,
            "ghostea_security_locks",
            {
                "chat_id": f"eq.{chat_id}",
                "lock_type": f"eq.{lock_type}",
            },
        )

    async def get_expired_verifications(self, now_iso, limit=100):
        return await self._call(
            self.db.select,
            "ghostea_verifications",
            {
                "verified": "eq.false",
                "expires_at": f"lt.{now_iso}",
                "order": "expires_at.asc",
                "limit": str(max(1, min(int(limit), 500))),
            },
        )

    async def get_user_directory(self, chat_id, limit=100):
        """Return observed users with bounded, index-friendly enrichment queries."""
        limit = max(1, min(int(limit), 500))
        try:
            directory = await self._call(
                self.db.select,
                "ghostea_user_directory",
                {
                    "chat_id": f"eq.{chat_id}",
                    "order": "last_activity.desc",
                    "limit": str(limit),
                },
            )
            ids = [int(row["user_id"]) for row in directory if row.get("user_id") is not None]
            if not ids:
                return []
            id_filter = "in.(" + ",".join(str(x) for x in ids) + ")"

            warnings = await self._call(
                self.db.select,
                "ghostea_warnings",
                {
                    "chat_id": f"eq.{chat_id}",
                    "user_id": id_filter,
                    "select": "user_id,count",
                    "limit": str(limit),
                },
            )
            reps = await self._call(
                self.db.select,
                "ghostea_reputation",
                {
                    "chat_id": f"eq.{chat_id}",
                    "user_id": id_filter,
                    "select": "user_id,score",
                    "limit": str(limit),
                },
            )
            # Action counts are derived from logs only for the bounded set of
            # users displayed on this page. This avoids a full-table GROUP BY.
            logs = await self._call(
                self.db.select,
                "ghostea_moderation_logs",
                {
                    "chat_id": f"eq.{chat_id}",
                    "user_id": id_filter,
                    "select": "user_id,id",
                    "limit": "1000",
                },
            )
            warning_map = {str(row["user_id"]): int(row.get("count", 0)) for row in warnings}
            rep_map = {str(row["user_id"]): int(row.get("score", 0)) for row in reps}
            action_map = {}
            for row in logs:
                key = str(row.get("user_id"))
                action_map[key] = action_map.get(key, 0) + 1

            result = []
            for row in directory:
                key = str(row["user_id"])
                result.append({
                    "chat_id": int(chat_id),
                    "user_id": int(row["user_id"]),
                    "first_seen_at": row.get("first_seen_at"),
                    "last_activity": row.get("last_activity"),
                    "warnings": warning_map.get(key, 0),
                    "actions": action_map.get(key, 0),
                    "reputation": rep_map.get(key, 0),
                })
            return result
        except Exception:
            # Backward-compatible fallback until the Phase 14 directory table
            # exists. Keep the fallback bounded so it cannot fan out endlessly.
            warnings = await self._call(
                self.db.select,
                "ghostea_warning_history",
                {"chat_id": f"eq.{chat_id}", "order": "created_at.desc", "limit": "200"},
            )
            logs = await self._call(
                self.db.select,
                "ghostea_moderation_logs",
                {"chat_id": f"eq.{chat_id}", "order": "created_at.desc", "limit": "200"},
            )
            users = {}
            def ensure(uid, ts=None):
                item = users.setdefault(str(uid), {
                    "user_id": int(uid), "warnings": 0, "actions": 0,
                    "reputation": 0, "last_activity": ts,
                })
                if ts and (not item["last_activity"] or str(ts) > str(item["last_activity"])):
                    item["last_activity"] = ts
                return item
            for row in warnings:
                uid = row.get("user_id")
                if uid is not None:
                    ensure(uid, row.get("created_at"))["warnings"] += 1
            for row in logs:
                uid = row.get("user_id")
                if uid is not None:
                    ensure(uid, row.get("created_at"))["actions"] += 1
            return sorted(
                users.values(),
                key=lambda x: (x["last_activity"] or "", x["warnings"], x["actions"]),
                reverse=True,
            )[:limit]

    async def get_reputation(self, chat_id, user_id):
        rows = await self._call(
            self.db.select,
            "ghostea_reputation",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        if rows:
            return rows[0]
        default = {
            "chat_id": chat_id,
            "user_id": user_id,
            "score": 0,
            "positive_actions": 0,
            "negative_actions": 0,
        }
        return default

    async def change_reputation(self, chat_id, user_id, delta):
        key = (int(chat_id), int(user_id))
        lock = self._reputation_locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = await self.get_reputation(chat_id, user_id)
            score = int(current["score"]) + int(delta)
            positive = int(current["positive_actions"]) + (1 if delta > 0 else 0)
            negative = int(current["negative_actions"]) + (1 if delta < 0 else 0)

            await self._call(
                self.db.upsert,
                "ghostea_reputation",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "score": score,
                    "positive_actions": positive,
                    "negative_actions": negative,
                },
            )
            return score

    async def record_cleanup(self, chat_id, cutoff_at, deleted_count):
        return await self._call(
            self.db.insert,
            "ghostea_cleanup_runs",
            {
                "chat_id": chat_id,
                "cutoff_at": cutoff_at,
                "deleted_count": deleted_count,
            },
        )


    async def get_analytics(self, chat_id, since_iso):
        warnings = await self._call(
            self.db.select,
            "ghostea_warning_history",
            {"chat_id": f"eq.{chat_id}", "created_at": f"gte.{since_iso}",
             "select": "id,user_id,reason,source,created_at",
             "order": "created_at.desc", "limit": "1000"},
        )
        logs = await self._call(
            self.db.select,
            "ghostea_moderation_logs",
            {"chat_id": f"eq.{chat_id}", "created_at": f"gte.{since_iso}",
             "select": "id,user_id,action,reason,created_at",
             "order": "created_at.desc", "limit": "1000"},
        )
        joins = await self._call(
            self.db.select,
            "ghostea_join_events",
            {"chat_id": f"eq.{chat_id}", "joined_at": f"gte.{since_iso}",
             "select": "id,user_id,joined_at",
             "order": "joined_at.desc", "limit": "1000"},
        )
        return {"warnings": warnings, "logs": logs, "joins": joins}

    async def log_health(self, status, details="", chat_id=None):
        return await self._call(
            self.db.insert, "ghostea_health_events",
            {"chat_id": chat_id, "status": status, "details": details},
        )


    async def get_user_profile(self, chat_id, user_id, log_limit=100):
        warnings = await self.get_warning_count(chat_id, user_id)
        history = await self.get_history(chat_id, user_id, limit=log_limit)
        reputation = await self.get_reputation(chat_id, user_id)
        logs = await self._call(
            self.db.select,
            "ghostea_moderation_logs",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": str(log_limit),
            },
        )
        return {
            "chat_id": chat_id,
            "user_id": user_id,
            "warnings": warnings,
            "warning_history": history,
            "reputation": reputation,
            "moderation_logs": logs,
        }

    async def log_user_admin_action(
        self, chat_id, target_user_id, admin_user_id, action, details=""
    ):
        return await self._call(
            self.db.insert,
            "ghostea_user_admin_actions",
            {
                "chat_id": chat_id,
                "target_user_id": target_user_id,
                "admin_user_id": admin_user_id,
                "action": action,
                "details": details,
            },
        )

    async def recent_user_admin_actions(
        self, chat_id, target_user_id, limit=100
    ):
        return await self._call(
            self.db.select,
            "ghostea_user_admin_actions",
            {
                "chat_id": f"eq.{chat_id}",
                "target_user_id": f"eq.{target_user_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

