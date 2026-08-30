from ghostea.config import MAX_WARNINGS, MUTE_MINUTES


class ModerationService:
    """Central punishment logic shared by automatic and manual warnings."""

    def __init__(self, warning_store):
        self.warning_store = warning_store

    async def issue_warning(
        self,
        chat,
        user,
        reason: str,
        source: str,
    ) -> tuple[int, str]:
        count = self.warning_store.add_warning(
            chat.id,
            user.id,
            reason,
            source,
        )

        if count >= MAX_WARNINGS:
            await chat.ban_member(user_id=user.id)
            return count, "ban"

        minutes = MUTE_MINUTES[count]
        await chat.restrict_member(
            user_id=user.id,
            permissions=self._muted_permissions(),
            until_date=self._until(minutes),
        )
        return count, f"mute:{minutes}"

    @staticmethod
    def _until(minutes: int):
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) + timedelta(minutes=minutes)

    @staticmethod
    def _muted_permissions():
        from ghostea.services.telegram_service import muted_permissions
        return muted_permissions()

    def remove_warning(self, chat_id: int, user_id: int) -> int:
        return self.warning_store.remove_warning(chat_id, user_id)

    def reset_warnings(self, chat_id: int, user_id: int) -> None:
        self.warning_store.reset(chat_id, user_id)

    def get_warnings(self, chat_id: int, user_id: int) -> int:
        return self.warning_store.get_count(chat_id, user_id)

    def get_history(self, chat_id: int, user_id: int) -> list[dict]:
        return self.warning_store.get_history(chat_id, user_id)
