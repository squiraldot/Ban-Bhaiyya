import re
import time
from collections import defaultdict, deque


class ProtectionService:
    """Phase-2 spam, link and flood detection."""

    def __init__(
        self,
        spam_patterns,
        blocked_domains,
        window_seconds: int,
        message_limit: int,
    ):
        self.spam_patterns = spam_patterns
        self.blocked_domains = blocked_domains
        self.window_seconds = window_seconds
        self.message_limit = message_limit
        self._messages = defaultdict(deque)
        self._joins = defaultdict(deque)

    def find_spam_pattern(self, text: str) -> str | None:
        for pattern in self.spam_patterns.items:
            try:
                if re.search(pattern, text):
                    return pattern
            except re.error:
                continue
        return None

    def find_blocked_domain(self, text: str) -> str | None:
        lowered = text.casefold()

        for domain in self.blocked_domains.items:
            escaped = re.escape(domain.casefold())

            if re.search(
                rf"(?<![a-z0-9.-]){escaped}(?![a-z0-9.-])",
                lowered,
            ):
                return domain

        return None

    def register_message(self, chat_id: int, user_id: int) -> bool:
        """
        Returns True when the user reaches the flood threshold.

        Counters are kept separately for each chat/user pair and use
        monotonic time, so system-clock changes don't affect the window.
        """
        key = (chat_id, user_id)
        now = time.monotonic()

        queue = self._messages[key]
        queue.append(now)

        cutoff = now - self.window_seconds

        while queue and queue[0] < cutoff:
            queue.popleft()

        if len(queue) >= self.message_limit:
            queue.clear()
            return True

        return False


    def register_join(self, chat_id, user_id, window_seconds, join_limit):
        key = (chat_id,)
        now = time.monotonic()
        q = self._joins[key]
        q.append((now, user_id))
        cutoff = now - window_seconds
        while q and q[0][0] < cutoff:
            q.popleft()
        unique_users = {uid for _, uid in q}
        return len(unique_users) >= join_limit


    def find_mention_spam(self, text, mention_limit):
        # Telegram text entities are handled by the caller when available;
        # this fallback catches @username-style bursts.
        return len(re.findall(r"(?<!\w)@[A-Za-z0-9_]{4,32}", text)) >= mention_limit

    def register_repeated_message(self, chat_id, user_id, text, window_seconds, limit):
        key = ("repeat", chat_id, user_id)
        now = time.monotonic()
        normalized = re.sub(r"\s+", " ", text.casefold()).strip()
        q = self._messages[key]
        q.append((now, normalized))

        cutoff = now - window_seconds
        while q and q[0][0] < cutoff:
            q.popleft()

        same = sum(1 for _, value in q if value == normalized)
        if same >= limit:
            q.clear()
            return True
        return False
