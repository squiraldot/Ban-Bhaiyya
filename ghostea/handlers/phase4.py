import logging
from telegram import Update
from telegram.ext import ContextTypes

from ghostea.config import (
    ANTIRAID_ENABLED_DEFAULT,
    ANTIRAID_JOIN_LIMIT,
    ANTIRAID_LOCK_MINUTES,
    ANTIRAID_WINDOW_SECONDS,
    WELCOME_ENABLED_DEFAULT,
)
from ghostea.services.telegram_service import is_admin
from ghostea.utils import display_name

logger = logging.getLogger("Ghostea")


async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type not in ("group", "supergroup"):
        return

    store = context.application.bot_data["phase3_store"]
    protection = context.application.bot_data["protection"]
    settings = await store.get_settings(chat.id)

    from ghostea.handlers.phase5 import verification_for_member

    for member in message.new_chat_members:
        # Ignore bots joining; this avoids bot-to-bot join bursts.
        if member.is_bot:
            continue

        await store.record_join(chat.id, member.id)

        if settings.get("antiraid_enabled", ANTIRAID_ENABLED_DEFAULT):
            triggered = protection.register_join(
                chat.id,
                member.id,
                int(settings.get("antiraid_window_seconds", ANTIRAID_WINDOW_SECONDS)),
                int(settings.get("antiraid_join_limit", ANTIRAID_JOIN_LIMIT)),
            )
            if triggered:
                await activate_raid_mode(chat, context, settings)
                break

        await verification_for_member(chat, member, context)

        if settings.get("welcome_enabled", WELCOME_ENABLED_DEFAULT):
            try:
                await chat.send_message(
                    f"👋 Welcome {display_name(member)}!\n"
                    "Please read the group rules and keep the chat respectful. ❤️"
                )
            except Exception:
                logger.exception("Welcome message failed")


async def activate_raid_mode(chat, context, settings):
    minutes = int(settings.get("antiraid_lock_minutes", ANTIRAID_LOCK_MINUTES))
    security = context.application.bot_data["security"]

    try:
        await security.activate_raid(chat, minutes)
        await chat.send_message(
            "🚨 Anti-Raid activated!\n\n"
            f"Too many members joined in a short time.\n"
            f"Group temporarily locked for {minutes} minutes."
        )
    except Exception as error:
        logger.exception("Anti-Raid activation failed: %s", error)


async def raidmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text("Usage: /raidmode on|off")
        return

    enabled = context.args[0].lower() == "on"
    store = context.application.bot_data["phase3_store"]
    await store.update_settings(update.effective_chat.id, {"antiraid_enabled": enabled})
    await update.effective_message.reply_text(
        f"🛡️ Anti-Raid: {'ON' if enabled else 'OFF'}"
    )


async def setraid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /setraid 8 20 [10]")
        return
    try:
        limit = int(context.args[0])
        window = int(context.args[1])
        lock = int(context.args[2]) if len(context.args) > 2 else ANTIRAID_LOCK_MINUTES
        if not (2 <= limit <= 1000 and 5 <= window <= 3600 and 1 <= lock <= 1440):
            raise ValueError

        store = context.application.bot_data["phase3_store"]
        await store.update_settings(
            update.effective_chat.id,
            {
                "antiraid_join_limit": limit,
                "antiraid_window_seconds": window,
                "antiraid_lock_minutes": lock,
            },
        )
        await update.effective_message.reply_text(
            f"🛡️ Anti-Raid: {limit} joins / {window}s → {lock}m lock."
        )
    except ValueError:
        await update.effective_message.reply_text("Usage: /setraid 8 20 [10]")


async def welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text("Usage: /welcome on|off")
        return

    enabled = context.args[0].lower() == "on"
    store = context.application.bot_data["phase3_store"]
    await store.update_settings(update.effective_chat.id, {"welcome_enabled": enabled})
    await update.effective_message.reply_text(
        f"👋 Welcome system: {'ON' if enabled else 'OFF'}"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return
    store = context.application.bot_data["phase3_store"]
    stats = await store.get_stats(update.effective_chat.id)
    await update.effective_message.reply_text(
        "📊 Ghostea Statistics\n\n"
        f"Members joined: {stats['joins']}\n"
        f"Warnings: {stats['warnings']}\n"
        f"Moderation actions: {stats['actions']}"
    )


async def _admin(update):
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        return await is_admin(update.effective_chat, update.effective_user.id)
    except Exception:
        return False
