from datetime import datetime, timezone

from telegram import ChatMember
from telegram.constants import ChatMemberStatus

from ghostea.services.telegram_service import (
    ban_member,
    mute_member,
    unban_member,
    unmute_member,
    is_admin,
)


class UserManagementService:
    """Administrative user-management operations for one Telegram group."""

    def __init__(self, store, bot):
        self.store = store
        self.bot = bot

    async def profile(self, chat_id, user_id):
        data = await self.store.get_user_profile(chat_id, user_id)

        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
            data["telegram"] = {
                "status": member.status,
                "is_member": member.status
                in (
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.RESTRICTED,
                ),
                "user": {
                    "id": member.user.id,
                    "username": member.user.username,
                    "first_name": member.user.first_name,
                    "last_name": member.user.last_name,
                    "is_bot": member.user.is_bot,
                },
            }
        except Exception:
            data["telegram"] = None

        return data

    async def _guard_target(self, chat_id, target_user_id):
        try:
            if await is_admin(await self.bot.get_chat(chat_id), target_user_id):
                return False, "target_is_admin"
        except Exception:
            # get_chat() may fail in some Telegram contexts; fall through to
            # get_chat_member below.
            pass

        try:
            member = await self.bot.get_chat_member(chat_id, target_user_id)
            if member.status in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                return False, "target_is_admin"
        except Exception:
            pass

        return True, ""

    async def warn(self, chat_id, target_user_id, admin_user_id, reason):
        allowed, error = await self._guard_target(chat_id, target_user_id)
        if not allowed:
            raise PermissionError(error)

        member = await self.bot.get_chat_member(chat_id, target_user_id)
        count, action = await self._warning_for_member(
            chat_id, member.user, reason
        )
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "WARN",
            f"reason={reason};count={count};action={action}",
        )
        return await self.profile(chat_id, target_user_id)

    async def _warning_for_member(self, chat_id, user, reason):
        # Keep the same warning policy as Telegram /warn.
        from ghostea.services.phase3_moderation import Phase3ModerationService
        service = Phase3ModerationService(self.store)
        # A lightweight chat adapter is unnecessary: the bot's Chat object
        # provides the same moderation methods required by the service.
        chat = await self.bot.get_chat(chat_id)
        return await service.issue_warning(chat, user, reason, "dashboard")

    async def reset_warnings(self, chat_id, target_user_id, admin_user_id):
        allowed, error = await self._guard_target(chat_id, target_user_id)
        if not allowed:
            raise PermissionError(error)
        await self.store.reset_warnings(chat_id, target_user_id)
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "RESET_WARNINGS"
        )
        return await self.profile(chat_id, target_user_id)

    async def remove_warning(self, chat_id, target_user_id, admin_user_id):
        allowed, error = await self._guard_target(chat_id, target_user_id)
        if not allowed:
            raise PermissionError(error)
        count = await self.store.remove_warning(chat_id, target_user_id)
        await self.store.log(
            chat_id, target_user_id, "UNWARN", "Dashboard removed warning", ""
        )
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "UNWARN",
            f"remaining={count}",
        )
        return await self.profile(chat_id, target_user_id)

    async def ban(self, chat_id, target_user_id, admin_user_id):
        allowed, error = await self._guard_target(chat_id, target_user_id)
        if not allowed:
            raise PermissionError(error)
        chat = await self.bot.get_chat(chat_id)
        await ban_member(chat, target_user_id)
        await self.store.log(
            chat_id, target_user_id, "BAN", "Dashboard ban", ""
        )
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "BAN"
        )
        return await self.profile(chat_id, target_user_id)

    async def unban(self, chat_id, target_user_id, admin_user_id):
        chat = await self.bot.get_chat(chat_id)
        await unban_member(chat, target_user_id)
        await self.store.log(
            chat_id, target_user_id, "UNBAN", "Dashboard unban", ""
        )
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "UNBAN"
        )
        return await self.profile(chat_id, target_user_id)

    async def mute(self, chat_id, target_user_id, admin_user_id, minutes):
        allowed, error = await self._guard_target(chat_id, target_user_id)
        if not allowed:
            raise PermissionError(error)
        chat = await self.bot.get_chat(chat_id)
        await mute_member(chat, target_user_id, minutes)
        await self.store.log(
            chat_id, target_user_id, "MUTE", "Dashboard mute",
            f"minutes={minutes}",
        )
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "MUTE",
            f"minutes={minutes}",
        )
        return await self.profile(chat_id, target_user_id)

    async def unmute(self, chat_id, target_user_id, admin_user_id):
        allowed, error = await self._guard_target(chat_id, target_user_id)
        if not allowed:
            raise PermissionError(error)
        chat = await self.bot.get_chat(chat_id)
        await unmute_member(chat, target_user_id)
        await self.store.log(
            chat_id, target_user_id, "UNMUTE", "Dashboard unmute", ""
        )
        await self.store.log_user_admin_action(
            chat_id, target_user_id, admin_user_id, "UNMUTE"
        )
        return await self.profile(chat_id, target_user_id)
