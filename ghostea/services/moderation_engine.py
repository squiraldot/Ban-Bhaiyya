import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Detection:
    category: str
    reason: str
    score: int
    action: str
    source: str


class ModerationEngine:
    """
    Central decision layer for message content.

    Detection scores are used to rank/describe violations; they do not bypass
    the normal warning limit. This keeps automatic bans controlled by the
    existing warning policy.
    """

    WEIGHTS = {
        "abuse": 60,
        "spam": 45,
        "blocked_link": 35,
        "mention_spam": 30,
        "repeat_spam": 30,
        "long_message": 20,
    }

    def __init__(self, abuse_filter, protection):
        self.abuse_filter = abuse_filter
        self.protection = protection

    @staticmethod
    def _custom_word(text, rows):
        normalized = re.sub(r"[^a-z0-9]+", "", text.casefold())
        for row in rows:
            value = str(row.get("value", "")).strip()
            normalized_value = re.sub(r"[^a-z0-9]+", "", value.casefold())
            if not normalized_value:
                continue
            if len(normalized_value) <= 3:
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(normalized_value)}(?![a-z0-9])",
                    text.casefold(),
                ):
                    return value
            elif normalized_value in normalized:
                return value
        return None

    @staticmethod
    def _custom_domain(text, rows):
        lowered = text.casefold()
        for row in rows:
            value = str(row.get("value", "")).strip().casefold()
            if value and re.search(
                rf"(?<![a-z0-9.-]){re.escape(value)}(?![a-z0-9.-])",
                lowered,
            ):
                return value
        return None

    @staticmethod
    def _custom_pattern(text, rows):
        for row in rows:
            pattern = str(row.get("value", "")).strip()
            if not pattern:
                continue
            try:
                if re.search(pattern, text):
                    return pattern
            except re.error:
                continue
        return None

    def evaluate(
        self,
        text,
        settings,
        custom_words,
        custom_domains,
        custom_patterns,
    ) -> Optional[Detection]:
        detections = []

        max_length = int(settings.get("max_message_length", 4000))
        if len(text) > max_length:
            detections.append(
                Detection(
                    "long_message",
                    "Message exceeded configured length",
                    self.WEIGHTS["long_message"],
                    "delete",
                    "message_length",
                )
            )

        mention_limit = int(settings.get("mention_spam_limit", 6))
        if self.protection.find_mention_spam(text, mention_limit):
            detections.append(
                Detection(
                    "mention_spam",
                    "Mention spam",
                    self.WEIGHTS["mention_spam"],
                    "warn",
                    "mention_spam",
                )
            )

        if self.protection.register_repeated_message(
            # These values are supplied by evaluate's caller through settings.
            int(settings["_chat_id"]),
            int(settings["_user_id"]),
            text,
            int(settings.get("repeated_message_window_seconds", 60)),
            int(settings.get("repeated_message_limit", 3)),
        ):
            detections.append(
                Detection(
                    "repeat_spam",
                    "Repeated message spam",
                    self.WEIGHTS["repeat_spam"],
                    "warn",
                    "repeat_spam",
                )
            )

        if settings.get("link_filter_enabled", True):
            domain = (
                self.protection.find_blocked_domain(text)
                or self._custom_domain(text, custom_domains)
            )
            if domain:
                action = (
                    "warn"
                    if settings.get("blocked_link_action", "delete") == "warn"
                    else "delete"
                )
                detections.append(
                    Detection(
                        "blocked_link",
                        f"Blocked link: {domain}",
                        self.WEIGHTS["blocked_link"],
                        action,
                        "link",
                    )
                )

        if settings.get("spam_filter_enabled", True):
            pattern = (
                self.protection.find_spam_pattern(text)
                or self._custom_pattern(text, custom_patterns)
            )
            if pattern:
                detections.append(
                    Detection(
                        "spam",
                        "Spam/advertisement pattern",
                        self.WEIGHTS["spam"],
                        "warn",
                        "spam",
                    )
                )

        if settings.get("abuse_filter_enabled", True):
            detected = (
                self.abuse_filter.find(text)
                or self._custom_word(text, custom_words)
            )
            if detected:
                detections.append(
                    Detection(
                        "abuse",
                        f"Abusive language: {detected}",
                        self.WEIGHTS["abuse"],
                        "warn",
                        "automatic",
                    )
                )

        if not detections:
            return None

        # Higher-risk detections take precedence. Abuse wins ties over the
        # other categories because it is the core moderation signal.
        priority = {
            "abuse": 6,
            "spam": 5,
            "blocked_link": 4,
            "mention_spam": 3,
            "repeat_spam": 2,
            "long_message": 1,
        }
        return max(
            detections,
            key=lambda d: (d.score, priority.get(d.category, 0)),
        )
