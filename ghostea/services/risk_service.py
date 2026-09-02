import re
from collections import Counter
from datetime import datetime, timedelta, timezone


class RiskService:
    """
    Read-only moderation risk intelligence built from existing warning/log data.

    Risk is an advisory score for the dashboard. It never changes moderation
    actions by itself. Recent events carry more weight than old events.
    """

    ACTION_WEIGHTS = {
        "BAN": 100,
        "VERIFICATION_BAN": 90,
        "MUTE": 35,
        "FLOOD_MUTE": 35,
        "WARN": 25,
        "UNWARN": -15,
        "RESET_WARNINGS": -25,
        "DELETE": 10,
        "ABUSE_DELETE": 20,
        "SPAM_DELETE": 15,
        "BLOCKED_LINK": 15,
        "MENTION_SPAM": 15,
        "REPEAT_SPAM": 15,
        "LONG_MESSAGE": 8,
    }

    CATEGORY_ALIASES = {
        "abuse": "abuse",
        "spam": "spam",
        "blocked_link": "blocked_link",
        "mention_spam": "mention_spam",
        "repeat_spam": "repeat_spam",
        "long_message": "long_message",
        "flood": "flood",
        "verification": "verification",
    }

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _parse_time(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _category(action="", reason="", details=""):
        text = " ".join(
            str(x or "") for x in (action, reason, details)
        ).lower()
        for key, category in RiskService.CATEGORY_ALIASES.items():
            if key in text:
                return category
        return "other"

    @staticmethod
    def _logged_risk(details):
        match = re.search(r"(?:risk[_ ]?score)\s*[=:]\s*(\d+)", str(details or ""), re.I)
        if not match:
            return None
        return max(0, min(100, int(match.group(1))))

    def _event_weight(self, row):
        action = str(row.get("action") or "").upper()
        base = self.ACTION_WEIGHTS.get(action, 0)
        logged = self._logged_risk(row.get("details"))
        if logged is not None:
            base = max(base, logged)

        # Warnings are also represented in warning_history. Logs should not
        # double the warning too aggressively, so their base remains modest.
        return base

    def _decay(self, created_at, now):
        when = self._parse_time(created_at)
        if not when:
            return 0.35
        age_days = max(0.0, (now - when).total_seconds() / 86400)
        # Half-life of seven days.
        return 0.5 ** (age_days / 7.0)

    async def report(self, chat_id, days=7, limit=20):
        days = max(1, min(int(days), 90))
        limit = max(1, min(int(limit), 50))
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        now = datetime.now(timezone.utc)

        # Fetch only the bounded fields required by the risk engine. Keeping
        # the query narrow is important for groups with tens of thousands of
        # members.
        errors = []
        try:
            logs = await self.store._call(
                self.store.db.select,
                "ghostea_moderation_logs",
                {
                    "chat_id": f"eq.{chat_id}",
                    "created_at": f"gte.{since}",
                    "select": "user_id,action,reason,details,created_at",
                    "order": "created_at.desc",
                    "limit": "1000",
                },
            )
        except Exception:
            logger = __import__("logging").getLogger("Ghostea")
            logger.exception("Risk log query failed for chat=%s", chat_id)
            logs = []
            errors.append("logs")

        try:
            warnings = await self.store._call(
                self.store.db.select,
                "ghostea_warning_history",
                {
                    "chat_id": f"eq.{chat_id}",
                    "created_at": f"gte.{since}",
                    "select": "user_id,reason,source,created_at",
                    "order": "created_at.desc",
                    "limit": "1000",
                },
            )
        except Exception:
            logger = __import__("logging").getLogger("Ghostea")
            logger.exception("Risk warning query failed for chat=%s", chat_id)
            warnings = []
            errors.append("warnings")

        if errors and not logs and not warnings:
            raise RuntimeError("risk_data_unavailable")

        users = {}
        categories = Counter()

        def bucket(user_id):
            key = str(user_id)
            if key not in users:
                users[key] = {
                    "user_id": int(user_id),
                    "risk_score": 0.0,
                    "events": 0,
                    "warnings": 0,
                    "categories": Counter(),
                    "last_activity": None,
                }
            return users[key]

        for row in logs:
            user_id = row.get("user_id")
            if user_id is None:
                continue
            item = bucket(user_id)
            weight = self._event_weight(row)
            decay = self._decay(row.get("created_at"), now)
            item["risk_score"] += weight * decay
            item["events"] += 1
            category = self._category(
                row.get("action"), row.get("reason"), row.get("details")
            )
            item["categories"][category] += 1
            categories[category] += 1
            if not item["last_activity"] or str(row.get("created_at", "")) > str(item["last_activity"]):
                item["last_activity"] = row.get("created_at")

        for row in warnings:
            user_id = row.get("user_id")
            if user_id is None:
                continue
            item = bucket(user_id)
            decay = self._decay(row.get("created_at"), now)
            item["risk_score"] += 20 * decay
            item["warnings"] += 1
            category = self._category(
                "warning", row.get("reason"), row.get("source")
            )
            item["categories"][category] += 1
            categories[category] += 1
            if not item["last_activity"] or str(row.get("created_at", "")) > str(item["last_activity"]):
                item["last_activity"] = row.get("created_at")

        result = []
        for item in users.values():
            score = max(0, min(100, round(item["risk_score"])))
            if score >= 75:
                level = "critical"
            elif score >= 50:
                level = "high"
            elif score >= 25:
                level = "medium"
            else:
                level = "low"
            result.append({
                "user_id": item["user_id"],
                "risk_score": score,
                "risk_level": level,
                "events": item["events"],
                "warnings": item["warnings"],
                "categories": dict(item["categories"]),
                "last_activity": item["last_activity"],
            })

        result.sort(key=lambda x: (x["risk_score"], x["events"]), reverse=True)
        high_critical = sum(1 for item in result if item["risk_level"] in ("high", "critical"))
        return {
            "days": days,
            "users": result[:limit],
            "total_users": len(result),
            "high_critical_users": high_critical,
            "category_breakdown": dict(categories),
            "sampled_events": len(logs) + len(warnings),
            "partial": bool(errors),
            "failed_sources": errors,
            "sample_cap": 2000,
        }

    async def user(self, chat_id, user_id, days=30):
        """Calculate risk directly for one user, without the top-user cap."""
        days = max(1, min(int(days), 90))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        now = datetime.now(timezone.utc)
        logs = await self.store._call(
            self.store.db.select,
            "ghostea_moderation_logs",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{int(user_id)}",
                "created_at": f"gte.{since}",
                "select": "user_id,action,reason,details,created_at",
                "order": "created_at.desc",
                "limit": "1000",
            },
        )
        warnings = await self.store._call(
            self.store.db.select,
            "ghostea_warning_history",
            {
                "chat_id": f"eq.{chat_id}",
                "user_id": f"eq.{int(user_id)}",
                "created_at": f"gte.{since}",
                "select": "user_id,reason,source,created_at",
                "order": "created_at.desc",
                "limit": "1000",
            },
        )

        score = 0.0
        events = 0
        warning_count = 0
        categories = Counter()
        last_activity = None
        for row in logs:
            score += self._event_weight(row) * self._decay(row.get("created_at"), now)
            events += 1
            category = self._category(row.get("action"), row.get("reason"), row.get("details"))
            categories[category] += 1
            if not last_activity or str(row.get("created_at", "")) > str(last_activity):
                last_activity = row.get("created_at")
        for row in warnings:
            score += 20 * self._decay(row.get("created_at"), now)
            events += 1
            warning_count += 1
            category = self._category("warning", row.get("reason"), row.get("source"))
            categories[category] += 1
            if not last_activity or str(row.get("created_at", "")) > str(last_activity):
                last_activity = row.get("created_at")

        score = max(0, min(100, round(score)))
        if score >= 75:
            level = "critical"
        elif score >= 50:
            level = "high"
        elif score >= 25:
            level = "medium"
        else:
            level = "low"
        return {
            "user_id": int(user_id),
            "risk_score": score,
            "risk_level": level,
            "events": events,
            "warnings": warning_count,
            "categories": dict(categories),
            "last_activity": last_activity,
        }

