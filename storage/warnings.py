import json
from datetime import datetime, timezone
from pathlib import Path


class WarningStore:
    """
    JSON-backed warning store.

    Structure:
    {
      "chat_id": {
        "user_id": {
          "count": 2,
          "history": [
            {
              "reason": "Abusive language",
              "source": "automatic",
              "time": "2026-08-28T..."
            }
          ]
        }
      }
    }
    """

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {}

        try:
            with self.file_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
                if not isinstance(data, dict):
                    return {}

                # Migrate the previous Phase-0/initial format:
                # {"chat_id": {"user_id": 2}}
                # into the Phase-1 record format.
                migrated = {}
                changed = False

                for chat_id, users in data.items():
                    if not isinstance(users, dict):
                        continue

                    migrated[str(chat_id)] = {}
                    for user_id, value in users.items():
                        if isinstance(value, int):
                            migrated[str(chat_id)][str(user_id)] = {
                                "count": value,
                                "history": [],
                            }
                            changed = True
                        elif isinstance(value, dict):
                            migrated[str(chat_id)][str(user_id)] = {
                                "count": int(value.get("count", 0)),
                                "history": list(value.get("history", [])),
                            }

                if changed:
                    # Save migration immediately so future reads use the
                    # new stable format.
                    self.data = migrated
                    self._save()

                return migrated
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def _save(self) -> None:
        temp = self.file_path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(self.data, file, indent=2, ensure_ascii=False)
        temp.replace(self.file_path)

    def _user_record(self, chat_id: int, user_id: int) -> dict:
        chat = self.data.setdefault(str(chat_id), {})
        return chat.setdefault(
            str(user_id),
            {"count": 0, "history": []},
        )

    def get_count(self, chat_id: int, user_id: int) -> int:
        return int(self._user_record(chat_id, user_id).get("count", 0))

    def get_history(self, chat_id: int, user_id: int) -> list[dict]:
        return list(self._user_record(chat_id, user_id).get("history", []))

    def add_warning(
        self,
        chat_id: int,
        user_id: int,
        reason: str,
        source: str,
    ) -> int:
        record = self._user_record(chat_id, user_id)
        record["count"] = int(record.get("count", 0)) + 1
        record.setdefault("history", []).append(
            {
                "reason": reason,
                "source": source,
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save()
        return record["count"]

    def remove_warning(self, chat_id: int, user_id: int) -> int:
        record = self._user_record(chat_id, user_id)
        count = int(record.get("count", 0))

        if count <= 0:
            return 0

        record["count"] = count - 1
        history = record.setdefault("history", [])
        if history:
            history.pop()

        self._save()
        return record["count"]

    def reset(self, chat_id: int, user_id: int) -> None:
        record = self._user_record(chat_id, user_id)
        record["count"] = 0
        record["history"] = []
        self._save()
