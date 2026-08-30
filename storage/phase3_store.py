import asyncio
from datetime import datetime, timezone

from banbhai.config import (
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

    async def _call(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def get_settings(self, chat_id):
        rows = await self._call(
            self.db.select,
            "banbhai_group_settings",
            {"chat_id": f"eq.{chat_id}", "limit": "1"},
        )

        if rows:
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

        await self._call(self.db.upsert, "banbhai_group_settings", defaults)
        return defaults

    async def update_settings(self, chat_id, changes):
        changes = dict(changes)
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = await self._call(
            self.db.update,
            "banbhai_group_settings",
            changes,
            {"chat_id": f"eq.{chat_id}"},
        )
        return rows[0] if rows else await self.get_settings(chat_id)

    async def get_warning_count(self, chat_id, user_id):
        rows = await self._call(
            self.db.select,
            "banbhai_warnings",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        return int(rows[0]["count"]) if rows else 0

    async def add_warning(self, chat_id, user_id, reason, source):
        count = await self.get_warning_count(chat_id, user_id) + 1

        await self._call(
            self.db.upsert,
            "banbhai_warnings",
            {"chat_id": chat_id, "user_id": user_id, "count": count},
        )
        await self._call(
            self.db.insert,
            "banbhai_warning_history",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "reason": reason,
                "source": source,
            },
        )
        await self.log(
            chat_id, user_id, "WARN", reason,
            f"warning_count={count};source={source}",
        )
        return count

    async def remove_warning(self, chat_id, user_id):
        count = max(0, await self.get_warning_count(chat_id, user_id) - 1)
        await self._call(
            self.db.upsert,
            "banbhai_warnings",
            {"chat_id": chat_id, "user_id": user_id, "count": count},
        )
        return count

    async def reset_warnings(self, chat_id, user_id):
        await self._call(
            self.db.upsert,
            "banbhai_warnings",
            {"chat_id": chat_id, "user_id": user_id, "count": 0},
        )
        await self.log(chat_id, user_id, "RESET_WARNINGS", "Admin reset", "")

    async def get_history(self, chat_id, user_id, limit=10):
        return await self._call(
            self.db.select,
            "banbhai_warning_history",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def add_custom_filter(self, chat_id, filter_type, value):
        return await self._call(
            self.db.upsert,
            "banbhai_custom_filters",
            {
                "chat_id": chat_id,
                "filter_type": filter_type,
                "value": value,
                "enabled": True,
            },
        )

    async def remove_custom_filter(self, chat_id, filter_type, value):
        return await self._call(
            self.db.delete,
            "banbhai_custom_filters",
            {
                "chat_id": f"eq.{chat_id}",
                "filter_type": f"eq.{filter_type}",
                "value": f"eq.{value}",
            },
        )

    async def get_custom_filters(self, chat_id, filter_type=None):
        query = {
            "chat_id": f"eq.{chat_id}",
            "enabled": "eq.true",
            "order": "created_at.asc",
        }
        if filter_type:
            query["filter_type"] = f"eq.{filter_type}"

        return await self._call(
            self.db.select,
            "banbhai_custom_filters",
            query,
        )

    async def log(self, chat_id, user_id, action, reason="", details=""):
        return await self._call(
            self.db.insert,
            "banbhai_moderation_logs",
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "action": action,
                "reason": reason,
                "details": details,
            },
        )

    async def recent_logs(self, chat_id, limit=20):
        return await self._call(
            self.db.select,
            "banbhai_moderation_logs",
            {
                "chat_id": f"eq.{chat_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

    async def record_join(self, chat_id, user_id):
        return await self._call(
            self.db.insert,
            "banbhai_join_events",
            {"chat_id": chat_id, "user_id": user_id},
        )

    async def log_raid_event(self, chat_id, action, details=""):
        return await self._call(
            self.db.insert,
            "banbhai_raid_events",
            {"chat_id": chat_id, "action": action, "details": details},
        )

    async def get_stats(self, chat_id):
        warnings = await self._call(
            self.db.select,
            "banbhai_warning_history",
            {"chat_id": f"eq.{chat_id}", "select": "id", "limit": "1000"},
        )
        actions = await self._call(
            self.db.select,
            "banbhai_moderation_logs",
            {"chat_id": f"eq.{chat_id}", "select": "id", "limit": "1000"},
        )
        joins = await self._call(
            self.db.select,
            "banbhai_join_events",
            {"chat_id": f"eq.{chat_id}", "select": "id", "limit": "1000"},
        )
        return {"warnings": len(warnings), "actions": len(actions), "joins": len(joins)}


    async def save_verification(self, chat_id, user_id, token, expires_at):
        return await self._call(
            self.db.upsert,
            "banbhai_verifications",
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
            "banbhai_verifications",
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
            "banbhai_verifications",
            {"verified": True},
            {"chat_id": f"eq.{chat_id}", "user_id": f"eq.{user_id}"},
        )

    async def delete_verification(self, chat_id, user_id):
        return await self._call(
            self.db.delete,
            "banbhai_verifications",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{user_id}",
            },
        )

    async def get_reputation(self, chat_id, user_id):
        rows = await self._call(
            self.db.select,
            "banbhai_reputation",
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
        await self._call(self.db.upsert, "banbhai_reputation", default)
        return default

    async def change_reputation(self, chat_id, user_id, delta):
        current = await self.get_reputation(chat_id, user_id)
        score = int(current["score"]) + int(delta)
        positive = int(current["positive_actions"]) + (1 if delta > 0 else 0)
        negative = int(current["negative_actions"]) + (1 if delta < 0 else 0)

        await self._call(
            self.db.upsert,
            "banbhai_reputation",
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
            "banbhai_cleanup_runs",
            {
                "chat_id": chat_id,
                "cutoff_at": cutoff_at,
                "deleted_count": deleted_count,
            },
        )


    async def get_analytics(self, chat_id, since_iso):
        warnings = await self._call(
            self.db.select,
            "banbhai_warning_history",
            {"chat_id": f"eq.{chat_id}", "created_at": f"gte.{since_iso}",
             "select": "id,user_id,reason,source,created_at",
             "order": "created_at.desc", "limit": "1000"},
        )
        logs = await self._call(
            self.db.select,
            "banbhai_moderation_logs",
            {"chat_id": f"eq.{chat_id}", "created_at": f"gte.{since_iso}",
             "select": "id,user_id,action,reason,created_at",
             "order": "created_at.desc", "limit": "1000"},
        )
        joins = await self._call(
            self.db.select,
            "banbhai_join_events",
            {"chat_id": f"eq.{chat_id}", "joined_at": f"gte.{since_iso}",
             "select": "id,user_id,joined_at",
             "order": "joined_at.desc", "limit": "1000"},
        )
        return {"warnings": warnings, "logs": logs, "joins": joins}

    async def log_health(self, status, details="", chat_id=None):
        return await self._call(
            self.db.insert, "banbhai_health_events",
            {"chat_id": chat_id, "status": status, "details": details},
        )

