from ghostea.services.telegram_service import muted_permissions
from datetime import datetime, timedelta, timezone


class Phase3ModerationService:
    def __init__(self, store):
        self.store = store

    async def issue_warning(self, chat, user, reason, source):
        settings = await self.store.get_settings(chat.id)
        count = await self.store.add_warning(
            chat.id, user.id, reason, source
        )

        if count >= settings["max_warnings"]:
            await chat.ban_member(user_id=user.id)
            await self.store.log(
                chat.id, user.id, "BAN", reason,
                f"warning_count={count}",
            )
            return count, "ban"

        minutes = (
            settings["mute1_minutes"]
            if count == 1
            else settings["mute2_minutes"]
        )

        until_date = datetime.now(timezone.utc) + timedelta(minutes=minutes)

        await chat.restrict_member(
            user_id=user.id,
            permissions=muted_permissions(),
            until_date=until_date,
        )
        await self.store.log(
            chat.id, user.id, "MUTE", reason,
            f"warning_count={count};minutes={minutes}",
        )
        return count, f"mute:{minutes}"
