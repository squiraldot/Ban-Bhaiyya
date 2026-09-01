import asyncio
import logging
from datetime import datetime, timezone

from telegram import ChatMember, ChatPermissions
from telegram.constants import ChatMemberStatus

from ghostea.services.telegram_service import muted_permissions

logger = logging.getLogger("Ghostea")

_PERMISSION_FIELDS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
    "can_edit_tag",
    "can_react_to_messages",
)


def _permissions_to_dict(permissions):
    if not permissions:
        return {}
    result = {}
    for field in _PERMISSION_FIELDS:
        value = getattr(permissions, field, None)
        if value is not None:
            result[field] = bool(value)
    return result


def _permissions_from_dict(data):
    if not isinstance(data, dict):
        return ChatPermissions()
    return ChatPermissions(**{
        key: bool(value)
        for key, value in data.items()
        if key in _PERMISSION_FIELDS
    })


class SecurityService:
    """Persistent recovery for anti-raid locks and verification expiry."""

    def __init__(self, store, bot):
        self.store = store
        self.bot = bot
        self._task = None
        self._raid_tasks = {}

    async def start(self):
        await self.recover()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._watch_loop(),
                name="ghostea-security-watch",
            )

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

        for task in list(self._raid_tasks.values()):
            task.cancel()
        self._raid_tasks.clear()

    async def recover(self):
        """Recover persisted security state after a Render restart."""
        try:
            locks = await self.store.get_active_security_locks()
            for lock in locks:
                await self._schedule_raid_lock(lock)
        except Exception:
            logger.exception("Raid lock recovery failed")

        try:
            await self.expire_verifications()
        except Exception:
            logger.exception("Verification recovery failed")

    async def _watch_loop(self):
        while True:
            try:
                await self.expire_verifications()
                await self._recover_due_raid_locks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Security recovery loop failed")
            await asyncio.sleep(15)

    async def _recover_due_raid_locks(self):
        locks = await self.store.get_active_security_locks()
        known = set(self._raid_tasks)
        current = {int(row["chat_id"]) for row in locks}

        for row in locks:
            await self._schedule_raid_lock(row)

        for chat_id in known - current:
            task = self._raid_tasks.pop(chat_id, None)
            if task:
                task.cancel()

    async def _schedule_raid_lock(self, row):
        chat_id = int(row["chat_id"])
        existing = self._raid_tasks.get(chat_id)
        if existing and not existing.done():
            return

        async def unlock_when_due():
            try:
                expires = datetime.fromisoformat(
                    str(row["expires_at"]).replace("Z", "+00:00")
                )
                delay = max(
                    0,
                    (expires - datetime.now(timezone.utc)).total_seconds(),
                )
                if delay:
                    await asyncio.sleep(delay)
                await self.unlock_raid(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled raid unlock failed: chat=%s", chat_id)

        self._raid_tasks[chat_id] = asyncio.create_task(
            unlock_when_due(),
            name=f"ghostea-raid-unlock-{chat_id}",
        )

    async def activate_raid(self, chat, minutes: int):
        """Lock new messages while preserving the group's original defaults."""
        chat_info = await chat.get_chat()
        original = _permissions_to_dict(getattr(chat_info, "permissions", None))

        now = datetime.now(timezone.utc)
        expires = now.timestamp() + (int(minutes) * 60)
        expires_dt = datetime.fromtimestamp(expires, timezone.utc)

        existing = await self.store.get_security_lock(chat.id, "raid")
        if existing:
            original = existing.get("original_permissions") or original

        if not original:
            # If Telegram omitted permissions, do not guess the group's
            # original policy. The lock can still be applied and will be
            # safely removed by the persisted state only when an original
            # snapshot is available.
            original = {}

        await self.store.upsert_security_lock(
            chat.id,
            "raid",
            expires_dt.isoformat(),
            original,
        )

        try:
            await chat.set_permissions(permissions=muted_permissions())
        except Exception:
            await self.store.delete_security_lock(chat.id, "raid")
            raise

        task = self._raid_tasks.get(chat.id)
        if task and not task.done():
            task.cancel()
        await self._schedule_raid_lock(
            {
                "chat_id": chat.id,
                "expires_at": expires_dt.isoformat(),
                "original_permissions": original,
            }
        )

        await self.store.log_raid_event(
            chat.id,
            "RAID_LOCK",
            f"join burst exceeded limit; lock={int(minutes)}m",
        )

    async def unlock_raid(self, chat_id: int):
        row = await self.store.get_security_lock(chat_id, "raid")
        if not row:
            return False

        original = row.get("original_permissions") or {}
        try:
            chat = await self.bot.get_chat(chat_id)
            if original:
                await chat.set_permissions(
                    permissions=_permissions_from_dict(original)
                )
                await self.store.log_raid_event(
                    chat_id,
                    "RAID_UNLOCK",
                    "raid lock expired; original permissions restored",
                )
            else:
                logger.warning(
                    "Raid lock expired without permission snapshot: chat=%s",
                    chat_id,
                )
                await self.store.log_raid_event(
                    chat_id,
                    "RAID_UNLOCK_SKIPPED",
                    "no original permission snapshot available",
                )
        finally:
            await self.store.delete_security_lock(chat_id, "raid")
            task = self._raid_tasks.pop(int(chat_id), None)
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
        return True

    async def expire_verifications(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = await self.store.get_expired_verifications(now, limit=100)

        for row in rows:
            chat_id = int(row["chat_id"])
            user_id = int(row["user_id"])

            try:
                member = await self.bot.get_chat_member(chat_id, user_id)
                if member.status in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                ):
                    # Never auto-ban an administrator because of a stale or
                    # misconfigured verification record.
                    await self.store.delete_verification(chat_id, user_id)
                    continue

                if member.status in (
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.RESTRICTED,
                ):
                    await self.bot.ban_chat_member(
                        chat_id=chat_id,
                        user_id=user_id,
                    )
                    await self.store.log(
                        chat_id,
                        user_id,
                        "VERIFICATION_BAN",
                        "Verification expired",
                        "member did not complete verification",
                    )
            except Exception as error:
                # If the user already left or was banned, the cleanup below
                # should still happen. Telegram errors are logged for admins.
                logger.warning(
                    "Verification expiry action failed: chat=%s user=%s error=%s",
                    chat_id,
                    user_id,
                    error,
                )
            finally:
                await self.store.delete_verification(chat_id, user_id)
