from collections import Counter
from datetime import datetime, timedelta, timezone


class AnalyticsService:
    def __init__(self, store):
        self.store = store

    async def report(self, chat_id, days=7):
        days = max(1, min(int(days), 90))
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        data = await self.store.get_analytics(chat_id, since)

        warnings = Counter(
            row.get("reason") or "unknown"
            for row in data["warnings"]
        )
        actions = Counter(
            row.get("action") or "unknown"
            for row in data["logs"]
        )

        return {
            "days": days,
            "joins": len(data["joins"]),
            "warnings": len(data["warnings"]),
            "actions": len(data["logs"]),
            "warning_reasons": warnings,
            "actions_by_type": actions,
        }
