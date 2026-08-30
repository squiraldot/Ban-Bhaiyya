from pathlib import Path


class LineList:
    """Loads one item per line, ignoring blanks and # comments."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.items: list[str] = []
        self.reload()

    def reload(self) -> int:
        if not self.file_path.exists():
            self.items = []
            return 0

        with self.file_path.open("r", encoding="utf-8") as file:
            self.items = sorted(
                {
                    line.strip()
                    for line in file
                    if line.strip() and not line.lstrip().startswith("#")
                },
                key=len,
                reverse=True,
            )

        return len(self.items)
