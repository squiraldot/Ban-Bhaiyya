import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN", "8870053082:AAHsNdGpbYsWlhjPEIsSbwL1T5NqTE_Jgt8")

# Warnings are counted separately for every user in every chat.
MAX_WARNINGS = 3

# Temporary mute durations for warning 1 and 2.
MUTE_MINUTES = {
    1: 2, # 2 is a time for muting the user (2 minutes)
    2: 5, # 5 is a time for muting the user (5 minutes)
}

DATA_FILE = "warnings.json"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("BanBhai")

# ============================================================
# ABUSIVE WORDS
# ============================================================

ABUSIVE_WORDS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "abusive_words.txt",
)


def load_abusive_words() -> list[str]:
    """Load filtered words from abusive_words.txt, one entry per line."""
    try:
        with open(ABUSIVE_WORDS_FILE, "r", encoding="utf-8") as file:
            words = [line.strip() for line in file if line.strip()]
    except OSError as error:
        raise RuntimeError(
            f"Could not load abusive words file: {ABUSIVE_WORDS_FILE}"
        ) from error

    return sorted(set(words), key=len, reverse=True)


ABUSIVE_WORDS = load_abusive_words()

# ============================================================
# WARNING STORAGE
# ============================================================

def load_warnings() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("Could not load %s: %s", DATA_FILE, error)
        return {}


warnings = load_warnings()


def save_warnings() -> None:
    # Atomic-ish write: write a temporary file first, then replace.
    temp_file = f"{DATA_FILE}.tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(warnings, file, indent=2, ensure_ascii=False)

    os.replace(temp_file, DATA_FILE)


def get_warning_count(chat_id: int, user_id: int) -> int:
    chat_key = str(chat_id)
    user_key = str(user_id)

    return int(
        warnings
        .get(chat_key, {})
        .get(user_key, 0)
    )


def increment_warning(chat_id: int, user_id: int) -> int:
    chat_key = str(chat_id)
    user_key = str(user_id)

    warnings.setdefault(chat_key, {})
    warnings[chat_key][user_key] = (
        get_warning_count(chat_id, user_id) + 1
    )

    save_warnings()
    return warnings[chat_key][user_key]

# ============================================================
# TEXT NORMALIZATION / ABUSE DETECTION
# ============================================================

def normalize_text(text: str) -> str:
    """
    Lowercase and remove non-alphanumeric characters.

    This catches common evasion such as:
      c.h.u.t.i.y.a
      c-h-u-t-i-y-a
      c h u t i y a

    NOTE:
    Because normalization removes separators, substring matching is
    intentionally avoided for very short words to reduce false positives.
    """
    text = text.casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def find_abusive_word(text: str) -> str | None:
    normalized = normalize_text(text)

    for word in ABUSIVE_WORDS:
        normalized_word = normalize_text(word)

        # Avoid dangerous substring matches for 3-letter words.
        # Example: "ass" inside an innocent longer word.
        if len(normalized_word) <= 3:
            pattern = rf"(?<![a-z0-9]){re.escape(normalized_word)}(?![a-z0-9])"
            if re.search(pattern, text.casefold()):
                return word
            continue

        if normalized_word in normalized:
            return word

    return None

# ============================================================
# TELEGRAM PERMISSIONS
# ============================================================

def muted_permissions() -> ChatPermissions:
    """
    Explicitly disable every normal sending permission.
    This makes temporary/permanent mute reliable.
    """
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False,
    )


async def mute_user(chat, user_id: int, minutes: int) -> None:
    until_date = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    await chat.restrict_member(
        user_id=user_id,
        permissions=muted_permissions(),
        until_date=until_date,
    )


async def ban_user(chat, user_id: int) -> None:
    # IMPORTANT: this is a real Telegram ban, not a permanent mute.
    await chat.ban_member(user_id=user_id)

# ============================================================
# HELPERS
# ============================================================

def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"

    return user.full_name or user.first_name or str(user.id)


async def is_admin(chat, user_id: int) -> bool:
    try:
        member = await chat.get_member(user_id)

        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

    except Exception as error:
        logger.exception("Admin check failed: %s", error)
        return False


async def send_warning(chat, user, warning_count: int, detected_word: str) -> None:
    name = display_name(user)

    if warning_count == 1:
        await chat.send_message(
            f"⚠️ {name}\n\n"
            f"Abusive word detected.\n"
            f"Warning: {warning_count}/{MAX_WARNINGS}\n"
            f"Muted for {MUTE_MINUTES[1]} minutes."
        )

    elif warning_count == 2:
        await chat.send_message(
            f"⚠️ {name}\n\n"
            f"Abusive word detected.\n"
            f"Warning: {warning_count}/{MAX_WARNINGS}\n"
            f"Muted for {MUTE_MINUTES[2]} minutes."
        )

    else:
        await chat.send_message(
            f"🚫 {name}\n\n"
            f"Warning limit reached: {MAX_WARNINGS}/{MAX_WARNINGS}\n"
            f"User has been permanently banned from this group."
        )

# ============================================================
# COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "👋 BanBhai online hai!\n"
        "Group moderation is active."
    )


async def warnings_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_user or not update.effective_chat:
        return

    count = get_warning_count(
        update.effective_chat.id,
        update.effective_user.id,
    )

    await update.effective_message.reply_text(
        f"⚠️ Your warnings: {count}/{MAX_WARNINGS}"
    )

# ============================================================
# MESSAGE CHECKER
# ============================================================

async def check_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return

    # Ignore private chats. Moderation is intended for groups/supergroups.
    if chat.type not in ("group", "supergroup"):
        return

    # Handles normal text + captions on photos/videos/documents/etc.
    text = message.text or message.caption

    if not text:
        return

    # Never punish admins/owners.
    if await is_admin(chat, user.id):
        return

    detected_word = find_abusive_word(text)

    if detected_word is None:
        return

    logger.info(
        "Abuse detected | chat=%s | user=%s | word=%s",
        chat.id,
        user.id,
        detected_word,
    )

    # Delete the abusive message first.
    try:
        await message.delete()
    except Exception as error:
        logger.warning("Message delete failed: %s", error)

    # Increase this user's warning count only in this chat.
    warning_count = increment_warning(chat.id, user.id)

    try:
        if warning_count < MAX_WARNINGS:
            await mute_user(
                chat,
                user.id,
                MUTE_MINUTES[warning_count],
            )
        else:
            # Third warning = actual permanent ban.
            await ban_user(chat, user.id)

    except Exception as error:
        # Common causes:
        # - bot is not admin
        # - bot lacks Delete Messages / Restrict Members permission
        # - group is not a supergroup
        logger.exception(
            "Moderation action failed for user %s in chat %s: %s",
            user.id,
            chat.id,
            error,
        )

    try:
        await send_warning(
            chat,
            user,
            warning_count,
            detected_word,
        )
    except Exception as error:
        logger.warning("Warning message failed: %s", error)

# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception("Unhandled bot error: %s", context.error)

# ============================================================
# START BOT
# ============================================================

def main() -> None:
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN is not configured. "
            "Set the BOT_TOKEN environment variable or put your token in TOKEN."
        )

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("warnings", warnings_command))

    # Process normal messages and media captions.
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            check_message,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("BanBhai is running...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
