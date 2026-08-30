import json
from pathlib import Path


async def migrate_json_if_needed(store, json_path: Path):
    """
    One-time best-effort migration of old Phase-1/2 warning counts.
    Existing warning counts are preserved. Old history cannot be reconstructed.
    """
    if not json_path.exists():
        return 0

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    migrated = 0

    for chat_id, users in data.items():
        if not isinstance(users, dict):
            continue

        for user_id, value in users.items():
            if isinstance(value, dict):
                count = int(value.get("count", 0))
            elif isinstance(value, int):
                count = value
            else:
                continue

            if count <= 0:
                continue

            existing = await store.get_warning_count(int(chat_id), int(user_id))
            if existing == 0:
                # Upsert the exact old count without inventing warning history.
                await store._call(
                    store.db.upsert,
                    "banbhai_warnings",
                    {
                        "chat_id": int(chat_id),
                        "user_id": int(user_id),
                        "count": count,
                    },
                )
                migrated += 1

    return migrated
