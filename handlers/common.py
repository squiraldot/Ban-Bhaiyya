from telegram import Update
from telegram.ext import ContextTypes

from banbhai.config import MAX_WARNINGS
from banbhai.services.telegram_service import is_admin
from banbhai.utils import display_name, get_target_user


async def require_admin(update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user or chat.type not in ("group", "supergroup"):
        return False

    try:
        return await is_admin(chat, user.id)
    except Exception:
        return False


def target_from_update(update: Update):
    return get_target_user(update)


async def warnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    requester = update.effective_user

    if not chat or not requester or not update.effective_message:
        return

    target = target_from_update(update)

    # Members can only see their own warnings.
    # Admins can inspect a target by replying to that user's message.
    if target.id != requester.id:
        try:
            if not await is_admin(chat, requester.id):
                target = requester
        except Exception:
            target = requester

    count = context.application.bot_data["moderation"].get_warnings(
        chat.id, target.id
    )

    await update.effective_message.reply_text(
        f"⚠️ {display_name(target)}\n"
        f"Warnings: {count}/{MAX_WARNINGS}"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 BanBhai online hai!\n"
        "Phase 1 moderation is active."
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
        return

    from banbhai.services.telegram_service import is_admin
    if not await is_admin(update.effective_chat, update.effective_user.id):
        return

    store = context.application.bot_data["phase3_store"]
    s = await store.get_settings(update.effective_chat.id)

    await update.effective_message.reply_text(
        "⚙️ BanBhai Settings\n\n"
        f"Warnings: {s['max_warnings']}\n"
        f"1st mute: {s['mute1_minutes']} min\n"
        f"2nd mute: {s['mute2_minutes']} min\n"
        f"Flood: {s['flood_message_limit']} msgs / {s['flood_window_seconds']} sec\n"
        f"Flood mute: {s['flood_mute_minutes']} min\n"
        f"Link action: {s['blocked_link_action']}\n\n"
        f"Abuse filter: {'ON' if s['abuse_filter_enabled'] else 'OFF'}\n"
        f"Spam filter: {'ON' if s['spam_filter_enabled'] else 'OFF'}\n"
        f"Link filter: {'ON' if s['link_filter_enabled'] else 'OFF'}\n"
        f"Flood protection: {'ON' if s['flood_protection_enabled'] else 'OFF'}"
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return

    target = target_from_update(update)
    store = context.application.bot_data["phase3_store"]
    rows = await store.get_history(update.effective_chat.id, target.id, 10)

    if not rows:
        await update.effective_message.reply_text(
            f"📋 No warning history for {display_name(target)}."
        )
        return

    lines = [f"📋 Warning history — {display_name(target)}", ""]
    for i, row in enumerate(rows, 1):
        lines.append(
            f"{i}. {row['reason']} [{row['source']}]"
        )

    await update.effective_message.reply_text("\n".join(lines))
