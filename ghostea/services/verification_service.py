import secrets
from datetime import datetime, timedelta, timezone


class VerificationService:
    """
    Simple button-based verification challenge.
    A random token is generated server-side and stored in Supabase.
    """

    def __init__(self, store):
        self.store = store

    async def create(self, chat_id, user_id, timeout_seconds):
        token = secrets.token_urlsafe(18)
        expires = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        await self.store.save_verification(
            chat_id, user_id, token, expires.isoformat()
        )
        return token, expires

    async def check(self, chat_id, user_id, token):
        row = await self.store.get_verification(chat_id, user_id)
        # A missing verification record is never proof of verification.
        # Returning True here would let an arbitrary stale/forged callback
        # unmute a member.
        if not row:
            return False
        if row.get("verified"):
            return True

        if row["token"] != token:
            return False

        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            return False

        await self.store.verify_user(chat_id, user_id)
        return True
