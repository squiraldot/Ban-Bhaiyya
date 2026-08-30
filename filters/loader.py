import re
from pathlib import Path


class AbuseFilter:
    """Loads and checks filtered words from a plain text file."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.words: list[str] = []
        self.reload()

    def reload(self) -> int:
        if not self.file_path.exists():
            raise FileNotFoundError(
                f"Filter file not found: {self.file_path}"
            )

        with self.file_path.open("r", encoding="utf-8") as file:
            words = [
                line.strip()
                for line in file
                if line.strip() and not line.lstrip().startswith("#")
            ]

        self.words = sorted(set(words), key=len, reverse=True)
        return len(self.words)

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", text.casefold())

    def find(self, text: str) -> str | None:
        normalized = self.normalize(text)

        for word in self.words:
            normalized_word = self.normalize(word)

            # For short filters, require an actual word boundary in
            # the original text to reduce accidental matches.
            if len(normalized_word) <= 3:
                pattern = rf"(?<![a-z0-9]){re.escape(normalized_word)}(?![a-z0-9])"
                if re.search(pattern, text.casefold()):
                    return word
                continue

            if normalized_word in normalized:
                return word

        return None
