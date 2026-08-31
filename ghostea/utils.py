def display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or user.first_name or str(user.id)


def get_target_user(update):
    """
    Reliable Phase-1 target selection:
    - If the command is a reply, target the replied user's account.
    - Otherwise target the command sender.

    For admin actions on another member, replying to that member's message
    is strongly recommended because Telegram bots cannot reliably resolve
    arbitrary @usernames to IDs.
    """
    message = update.effective_message

    if message and message.reply_to_message:
        return message.reply_to_message.from_user

    return update.effective_user


def target_from_update(update):
    """Return replied-to user when available, otherwise sender."""
    message = getattr(update, "effective_message", None)
    reply = getattr(message, "reply_to_message", None) if message else None
    target = getattr(reply, "from_user", None) if reply else None
    return target or getattr(update, "effective_user", None)
