import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from ghostea.config import MAX_WARNINGS
from ghostea.services.telegram_service import is_admin, mute_member

logger = logging.getLogger("Ghostea")


def display_name(user):
    return f"@{user.username}" if user.username else user.full_name


def normalize(text):
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def find_custom_word(text, rows):
    normalized = normalize(text)
    for row in rows:
        word = row["value"]
        if len(normalize(word)) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(word.casefold())}(?![a-z0-9])", text.casefold()):
                return word
        elif normalize(word) in normalized:
            return word
    return None


def find_custom_domain(text, rows):
    lowered = text.casefold()
    for row in rows:
        domain = row["value"].casefold()
        if re.search(rf"(?<![a-z0-9.-]){re.escape(domain)}(?![a-z0-9.-])", lowered):
            return domain
    return None


def find_custom_pattern(text, rows):
    for row in rows:
        try:
            if re.search(row["value"], text):
                return row["value"]
        except re.error:
            continue
    return None


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

    store = context.application.bot_data["phase3_store"]
    settings = await store.get_settings(chat.id)
    protection = context.application.bot_data["protection"]
    moderation = context.application.bot_data["phase3_moderation"]

    # Phase 5: message length
    if len(text) > int(settings.get("max_message_length", 4000)):
        try:
            await message.delete()
        except Exception:
            pass
        await store.log(
            chat.id, user.id, "DELETE_LONG_MESSAGE",
            "Message exceeded configured length",
            f"length={len(text)}",
        )
        return

    # Phase 5: mention spam
    if protection.find_mention_spam(
        text, int(settings.get("mention_spam_limit", 6))
    ):
        try:
            await message.delete()
        except Exception:
            pass
        count, action = await moderation.issue_warning(
            chat, user, "Mention spam", "mention_spam"
        )
        await announce(chat, user, count, action)
        return

    # Phase 5: repeated identical messages
    if protection.register_repeated_message(
        chat.id,
        user.id,
        text,
        int(settings.get("repeated_message_window_seconds", 60)),
        int(settings.get("repeated_message_limit", 3)),
    ):
        try:
            await message.delete()
        except Exception:
            pass
        count, action = await moderation.issue_warning(
            chat, user, "Repeated message spam", "repeat_spam"
        )
        await announce(chat, user, count, action)
        return

    try:
        if await is_admin(chat, user.id):
            return
    except Exception:
        return

    store = context.application.bot_data["phase3_store"]
    settings = await store.get_settings(chat.id)
    protection = context.application.bot_data["protection"]
    moderation = context.application.bot_data["phase3_moderation"]

    custom_words = await store.get_custom_filters(chat.id, "word")
    custom_domains = await store.get_custom_filters(chat.id, "domain")
    custom_patterns = await store.get_custom_filters(chat.id, "pattern")

    # Link protection
    if settings["link_filter_enabled"]:
        domain = protection.find_blocked_domain(text) or find_custom_domain(text, custom_domains)
        if domain:
            try:
                await message.delete()
            except Exception as error:
                logger.warning("Link delete failed: %s", error)

            if settings["blocked_link_action"] == "warn":
                try:
                    count, action = await moderation.issue_warning(
                        chat, user, f"Blocked link: {domain}", "link"
                    )
                    await announce(chat, user, count, action)
                except Exception:
                    logger.exception("Blocked-link warning failed")
            else:
                await chat.send_message(
                    f"🔗 Blocked link removed from {display_name(user)}."
                )

            await store.log(chat.id, user.id, "DELETE_LINK", f"Blocked link: {domain}", "")
            return

    # Spam protection
    if settings["spam_filter_enabled"]:
        pattern = (
            protection.find_spam_pattern(text)
            or find_custom_pattern(text, custom_patterns)
        )
        if pattern:
            try:
                await message.delete()
            except Exception as error:
                logger.warning("Spam delete failed: %s", error)

            try:
                count, action = await moderation.issue_warning(
                    chat, user, "Spam/advertisement pattern", "spam"
                )
                await announce(chat, user, count, action)
            except Exception:
                logger.exception("Spam moderation failed")
            return

    # Flood protection uses group-specific settings.
    if settings["flood_protection_enabled"]:
        # Update service thresholds dynamically.
        protection.window_seconds = int(settings["flood_window_seconds"])
        protection.message_limit = int(settings["flood_message_limit"])

        if protection.register_message(chat.id, user.id):
            minutes = int(settings["flood_mute_minutes"])
            try:
                await mute_member(chat, user.id, minutes)
                await chat.send_message(
                    f"🚨 {display_name(user)}\n\n"
                    f"Flood detected.\n"
                    f"Muted for {minutes} minutes."
                )
                await store.log(
                    chat.id, user.id, "FLOOD_MUTE",
                    "Flood protection",
                    f"minutes={minutes}",
                )
            except Exception:
                logger.exception("Flood mute failed")
            return

    # Abuse filter
    if settings["abuse_filter_enabled"]:
        detected = (
            context.application.bot_data["abuse_filter"].find(text)
            or find_custom_word(text, custom_words)
        )

        if detected is None:
            return

        try:
            await message.delete()
        except Exception as error:
            logger.warning("Abusive message delete failed: %s", error)

        try:
            count, action = await moderation.issue_warning(
                chat,
                user,
                f"Abusive language: {detected}",
                "automatic",
            )
            await announce(chat, user, count, action)
        except Exception:
            logger.exception("Automatic moderation failed")


async def announce(chat, user, count, action):
    name = display_name(user)

    if action == "ban":
        await chat.send_message(
            f"🚫 {name}\n\n"
            f"Warning limit reached: {count}/{MAX_WARNINGS}\n"
            f"User permanently banned."
        )
    else:
        minutes = int(action.split(":")[1])
        await chat.send_message(
            f"⚠️ {name}\n\n"
            f"Warning: {count}/{MAX_WARNINGS}\n"
            f"Muted for {minutes} minutes."
        )
