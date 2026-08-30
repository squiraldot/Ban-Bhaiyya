import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ghostea.config import (
    MAX_MESSAGE_LENGTH_DEFAULT,
    VERIFICATION_ENABLED_DEFAULT,
    VERIFICATION_TIMEOUT_SECONDS,
)
from ghostea.services.telegram_service import is_admin
from ghostea.utils import display_name

logger = logging.getLogger("Ghostea")


async def reputation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    target = update.effective_user
    if not chat or not target:
        return

    if update.message and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user

    store = context.application.bot_data["phase3_store"]
    rep = await store.get_reputation(chat.id, target.id)

    await update.effective_message.reply_text(
        f"⭐ Reputation — {display_name(target)}\n\n"
        f"Score: {rep['score']}\n"
        f"Positive actions: {rep['positive_actions']}\n"
        f"Negative actions: {rep['negative_actions']}"
    )


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data.startswith("verify:"):
        return

    await query.answer()
    token = query.data.split(":", 1)[1]
    chat = query.message.chat
    user = query.from_user

    verification = context.application.bot_data["verification"]
    ok = await verification.check(chat.id, user.id, token)

    if not ok:
        await query.answer("❌ Verification expired/invalid.", show_alert=True)
        return

    try:
        await chat.unban_member(user.id, only_if_banned=False)
    except Exception:
        pass

    try:
        await query.message.delete()
    except Exception:
        pass

    await query.message.chat.send_message(
        f"✅ {display_name(user)} verified successfully!"
    )


async def verification_for_member(chat, member, context):
    store = context.application.bot_data["phase3_store"]
    settings = await store.get_settings(chat.id)

    if not settings.get("verification_enabled", VERIFICATION_ENABLED_DEFAULT):
        return

    verification = context.application.bot_data["verification"]
    timeout = int(settings.get(
        "verification_timeout_seconds",
        VERIFICATION_TIMEOUT_SECONDS,
    ))

    token, expires = await verification.create(
        chat.id, member.id, timeout
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Verify me",
            callback_data=f"verify:{token}",
        )
    ]])

    try:
        await chat.send_message(
            f"🛡️ {display_name(member)}, please verify that you're human.\n"
            f"You have {timeout} seconds.",
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Verification message failed")


async def verification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text("Usage: /verification on|off")
        return

    enabled = context.args[0].lower() == "on"
    store = context.application.bot_data["phase3_store"]
    await store.update_settings(
        update.effective_chat.id,
        {"verification_enabled": enabled},
    )
    await update.effective_message.reply_text(
        f"🛡️ Verification: {'ON' if enabled else 'OFF'}"
    )


async def setverification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or not context.args:
        await update.effective_message.reply_text("Usage: /setverification 120")
        return

    try:
        seconds = int(context.args[0])
        if seconds < 30 or seconds > 3600:
            raise ValueError
        store = context.application.bot_data["phase3_store"]
        await store.update_settings(
            update.effective_chat.id,
            {"verification_timeout_seconds": seconds},
        )
        await update.effective_message.reply_text(
            f"🛡️ Verification timeout: {seconds} seconds."
        )
    except ValueError:
        await update.effective_message.reply_text("Usage: /setverification 120")


async def setmaxmsg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or not context.args:
        await update.effective_message.reply_text("Usage: /setmaxmsg 4000")
        return
    try:
        value = int(context.args[0])
        if value < 100 or value > 10000:
            raise ValueError
        store = context.application.bot_data["phase3_store"]
        await store.update_settings(
            update.effective_chat.id,
            {"max_message_length": value},
        )
        await update.effective_message.reply_text(
            f"📝 Maximum message length: {value} characters."
        )
    except ValueError:
        await update.effective_message.reply_text("Usage: /setmaxmsg 4000")


async def _admin(update):
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        return await is_admin(update.effective_chat, update.effective_user.id)
    except Exception:
        return False
