import logging

from telegram import Update
from telegram.ext import ContextTypes

from ghostea.services.telegram_service import is_admin
from ghostea.utils import target_from_update, display_name

logger = logging.getLogger("Ghostea")


async def _admin(update):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type not in ("group", "supergroup"):
        return False
    try:
        return await is_admin(chat, user.id)
    except Exception:
        return False


async def setwarnlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or not context.args:
        return
    try:
        value = int(context.args[0])
        if value < 1 or value > 20:
            raise ValueError
        store = context.application.bot_data["phase3_store"]
        await store.update_settings(update.effective_chat.id, {"max_warnings": value})
        await update.effective_message.reply_text(f"⚙️ Warning limit set to {value}.")
    except ValueError:
        await update.effective_message.reply_text("Usage: /setwarnlimit 3 (1-20)")


async def setmute1_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_positive(update, context, "mute1_minutes", "Usage: /setmute1 2")


async def setmute2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_positive(update, context, "mute2_minutes", "Usage: /setmute2 5")


async def setflood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /setflood 6 8")
        return
    try:
        limit, window = int(context.args[0]), int(context.args[1])
        if limit < 2 or limit > 100 or window < 1 or window > 300:
            raise ValueError
        store = context.application.bot_data["phase3_store"]
        await store.update_settings(
            update.effective_chat.id,
            {
                "flood_message_limit": limit,
                "flood_window_seconds": window,
            },
        )
        await update.effective_message.reply_text(
            f"⚙️ Flood limit set to {limit} messages / {window} seconds."
        )
    except ValueError:
        await update.effective_message.reply_text("Usage: /setflood 6 8")


async def setfloodmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_positive(
        update, context, "flood_mute_minutes",
        "Usage: /setfloodmute 10"
    )


async def setlinkaction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or not context.args:
        await update.effective_message.reply_text("Usage: /setlinkaction delete|warn")
        return
    value = context.args[0].lower()
    if value not in ("delete", "warn"):
        await update.effective_message.reply_text("Use only: delete or warn")
        return

    store = context.application.bot_data["phase3_store"]
    await store.update_settings(
        update.effective_chat.id,
        {"blocked_link_action": value},
    )
    await update.effective_message.reply_text(
        f"🔗 Blocked-link action: {value}"
    )


async def toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or len(context.args) != 2:
        await update.effective_message.reply_text(
            "Usage: /toggle abuse|spam|links|flood on|off"
        )
        return

    feature, state = context.args[0].lower(), context.args[1].lower()
    mapping = {
        "abuse": "abuse_filter_enabled",
        "spam": "spam_filter_enabled",
        "links": "link_filter_enabled",
        "flood": "flood_protection_enabled",
    }

    if feature not in mapping or state not in ("on", "off"):
        await update.effective_message.reply_text(
            "Usage: /toggle abuse|spam|links|flood on|off"
        )
        return

    store = context.application.bot_data["phase3_store"]
    await store.update_settings(
        update.effective_chat.id,
        {mapping[feature]: state == "on"},
    )
    await update.effective_message.reply_text(
        f"⚙️ {feature} protection: {state.upper()}"
    )


async def addfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: /addfilter word|domain|pattern VALUE"
        )
        return

    filter_type = context.args[0].lower()
    value = " ".join(context.args[1:]).strip()

    if filter_type not in ("word", "domain", "pattern") or not value:
        await update.effective_message.reply_text(
            "Type must be: word, domain, or pattern"
        )
        return

    store = context.application.bot_data["phase3_store"]
    try:
        await store.add_custom_filter(
            update.effective_chat.id,
            filter_type,
            value,
        )
        await update.effective_message.reply_text(
            f"✅ Custom {filter_type} filter added:\n{value}"
        )
    except Exception as error:
        logger.exception("Custom filter add failed: %s", error)
        await update.effective_message.reply_text(
            "❌ Could not add custom filter."
        )


async def delfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update) or len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: /delfilter word|domain|pattern VALUE"
        )
        return

    filter_type = context.args[0].lower()
    value = " ".join(context.args[1:]).strip()

    if filter_type not in ("word", "domain", "pattern"):
        await update.effective_message.reply_text(
            "Type must be: word, domain, or pattern"
        )
        return

    store = context.application.bot_data["phase3_store"]
    await store.remove_custom_filter(
        update.effective_chat.id,
        filter_type,
        value,
    )
    await update.effective_message.reply_text("🗑️ Custom filter removed.")


async def filters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return

    store = context.application.bot_data["phase3_store"]
    rows = await store.get_custom_filters(update.effective_chat.id)

    if not rows:
        await update.effective_message.reply_text("📁 No custom filters.")
        return

    lines = ["📁 Custom filters", ""]
    for row in rows:
        lines.append(
            f"#{row['id']} • {row['filter_type']}: {row['value']}"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return

    store = context.application.bot_data["phase3_store"]
    rows = await store.recent_logs(update.effective_chat.id, 15)

    if not rows:
        await update.effective_message.reply_text("📋 No moderation logs yet.")
        return

    lines = ["📋 Recent moderation logs", ""]
    for row in rows:
        lines.append(
            f"{row['action']} • user={row.get('user_id')} • "
            f"{row.get('reason') or '-'}"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def _set_positive(update, context, key, usage):
    if not await _admin(update) or not context.args:
        await update.effective_message.reply_text(usage)
        return
    try:
        value = int(context.args[0])
        if value < 1 or value > 10080:
            raise ValueError
        store = context.application.bot_data["phase3_store"]
        await store.update_settings(update.effective_chat.id, {key: value})
        await update.effective_message.reply_text(
            f"⚙️ {key.replace('_', ' ').title()} set to {value}."
        )
    except ValueError:
        await update.effective_message.reply_text(usage)
