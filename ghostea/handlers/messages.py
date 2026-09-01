import logging

from telegram import Update
from telegram.ext import ContextTypes

from ghostea.services.telegram_service import is_admin, mute_member
from ghostea.utils import display_name

logger = logging.getLogger("Ghostea")


async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return
    if chat.type not in ("group", "supergroup"):
        return

    text = message.text or message.caption
    if not text:
        return

    try:
        if await is_admin(chat, user.id):
            return
    except Exception:
        logger.exception("Admin check failed")
        return

    store = context.application.bot_data["phase3_store"]
    settings = await store.get_settings(chat.id)
    protection = context.application.bot_data["protection"]
    moderation = context.application.bot_data["phase3_moderation"]
    engine = context.application.bot_data["moderation_engine"]

    # Give the engine request-local identifiers without changing the persisted
    # group settings schema.
    evaluation_settings = dict(settings)
    evaluation_settings["_chat_id"] = chat.id
    evaluation_settings["_user_id"] = user.id

    custom_words = await store.get_custom_filters(chat.id, "word")
    custom_domains = await store.get_custom_filters(chat.id, "domain")
    custom_patterns = await store.get_custom_filters(chat.id, "pattern")

    detection = engine.evaluate(
        text,
        evaluation_settings,
        custom_words,
        custom_domains,
        custom_patterns,
    )

    if detection:
        try:
            await message.delete()
        except Exception:
            logger.exception(
                "Moderated message delete failed: category=%s",
                detection.category,
            )

        if detection.action == "warn":
            try:
                count, action = await moderation.issue_warning(
                    chat,
                    user,
                    detection.reason,
                    detection.source,
                )
                await announce(chat, user, count, action, settings, detection.score)
            except Exception:
                logger.exception(
                    "Automatic moderation failed: category=%s",
                    detection.category,
                )
        else:
            try:
                if detection.category == "blocked_link":
                    await chat.send_message(
                        f"🔗 Blocked link removed from {display_name(user)}."
                    )
            except Exception:
                logger.exception("Moderation notice failed")

            await store.log(
                chat.id,
                user.id,
                f"DELETE_{detection.category.upper()}",
                detection.reason,
                f"risk_score={detection.score}",
            )
        return

    # Flood is deliberately evaluated only after content moderation so a
    # single abusive/spam message cannot consume a flood action first.
    if settings.get("flood_protection_enabled", True):
        triggered = protection.register_message(
            chat.id,
            user.id,
            int(settings.get("flood_window_seconds", 8)),
            int(settings.get("flood_message_limit", 6)),
        )
        if triggered:
            minutes = int(settings.get("flood_mute_minutes", 10))
            try:
                await mute_member(chat, user.id, minutes)
                await chat.send_message(
                    f"🚨 {display_name(user)}\n\n"
                    f"Flood detected.\nMuted for {minutes} minutes."
                )
                await store.log(
                    chat.id,
                    user.id,
                    "FLOOD_MUTE",
                    "Flood protection",
                    f"minutes={minutes};risk_score=40",
                )
            except Exception:
                logger.exception("Flood mute failed")


async def announce(chat, user, count, action, settings, risk_score=None):
    name = display_name(user)
    limit = int(settings["max_warnings"])
    suffix = f"\nRisk score: {risk_score}" if risk_score is not None else ""

    try:
        if action == "ban":
            await chat.send_message(
                f"🚫 {name}\n\n"
                f"Warning limit reached: {count}/{limit}\n"
                "User permanently banned."
                f"{suffix}"
            )
        else:
            minutes = int(action.split(":", 1)[1])
            await chat.send_message(
                f"⚠️ {name}\n\n"
                f"Warning: {count}/{limit}\n"
                f"Muted for {minutes} minutes."
                f"{suffix}"
            )
    except Exception:
        logger.exception("Moderation announcement failed")
