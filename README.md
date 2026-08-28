# BanBhai — Fixed Telegram Moderation Bot

## What was fixed

1. **Permanent ban was not actually a ban**
   - The old code called `restrict_member()` for the third warning.
   - This version calls `ban_member()` on the third warning.

2. **Warnings were global**
   - The old code stored warnings only by `user_id`.
   - A user's warning count could therefore carry from one group to another.
   - This version stores warnings as `chat_id -> user_id -> count`.

3. **Mute permissions were incomplete**
   - The old code only disabled `can_send_messages`.
   - This version explicitly disables the normal message/media permissions.

4. **UTC-aware mute expiry**
   - Uses timezone-aware UTC datetimes instead of a naive local datetime.

5. **Media captions are checked**
   - Abuse in photo/video/document captions is now moderated too.

6. **Admin protection**
   - Group admins and the owner are ignored.

7. **Safer warning storage**
   - JSON writes use a temporary file and `os.replace()`.

8. **Better logging**
   - Telegram/API failures are printed with useful error details.

9. **Bot receives all update types**
   - `allowed_updates=Update.ALL_TYPES` is enabled.

10. **`/warnings` command**
   - Users can check their own warning count.

## Telegram setup — IMPORTANT

The code alone is not enough. Telegram permissions/settings can make a correct bot look broken.

### 1. Disable Privacy Mode

In **@BotFather**:

`/mybots` → select your bot → `Bot Settings` → `Group Privacy` → `Turn off`

The bot needs to receive ordinary group messages so it can inspect them.

### 2. Make the bot an administrator

Add the bot to the group and give it at least:

- Delete Messages
- Restrict Members

For the third warning it also needs permission to ban members.

### 3. Use a supergroup

For reliable moderation, use a Telegram supergroup.

### 4. Configure the token

Recommended:

```bash
export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
python banbhai_bot.py
```

Or replace:

```python
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
```

with your token.

### 5. Install dependencies

```bash
pip install -r requirements.txt
```

## Warning behavior

- 1st abusive message → delete + 2 minute mute
- 2nd abusive message → delete + 5 minute mute
- 3rd abusive message → delete + permanent Telegram ban

Warnings do **not** reset automatically.

## Important detection note

The detector catches common punctuation/spacing evasion such as:

- `c.h.u.t.i.y.a`
- `c-h-u-t-i-y-a`
- `c h u t i y a`

It intentionally avoids aggressive matching for very short words because substring detection can create false positives.

## Data

Warnings are stored in:

`warnings.json`

The file is created automatically after the first warning.

Do not commit your bot token or `warnings.json` to a public Git repository.
