from datetime import datetime, timedelta, timezone

from ghostea.config import DEFAULT_MAX_WARNINGS, DEFAULT_MUTE_MINUTES
from ghostea.services.telegram_service import muted_permissions


class ModerationService:
    """Legacy-compatible moderation service using the current defaults."""

    def __init__(self, warning_store):
        self.warning_store = warning_store

    async def issue_warning(self, chat, user, reason, source):
        count = self.warning_store.add_warning(chat.id, user.id, reason, source)
        if count >= DEFAULT_MAX_WARNINGS:
            await chat.ban_member(user_id=user.id)
            return count, "ban"

        minutes = DEFAULT_MUTE_MINUTES.get(count, DEFAULT_MUTE_MINUTES[2])
        await chat.restrict_member(
            user_id=user.id,
            permissions=muted_permissions(),
            until_date=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        )
        return count, f"mute:{minutes}"

    def remove_warning(self, chat_id, user_id):
        return self.warning_store.remove_warning(chat_id, user_id)

    def reset_warnings(self, chat_id, user_id):
        return self.warning_store.reset(chat_id, user_id)

    def get_warnings(self, chat_id, user_id):
        return self.warning_store.get_count(chat_id, user_id)

    def get_history(self, chat_id, user_id):
        return self.warning_store.get_history(chat_id, user_id)
