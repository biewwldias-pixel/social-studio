from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from studio.brands import STUDIO_DIR

CHATS_DIR = STUDIO_DIR / "chats"


def _brand_chats_file(brand_id: str):
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    return CHATS_DIR / f"{brand_id}.json"


def _load(brand_id: str) -> list[dict[str, Any]]:
    path = _brand_chats_file(brand_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(brand_id: str, items: list[dict[str, Any]]) -> None:
    path = _brand_chats_file(brand_id)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_chats(brand_id: str) -> list[dict[str, Any]]:
    items = _load(brand_id)
    items.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
    return items


def upsert_chat(
    brand_id: str,
    *,
    chat_id: str | None,
    title: str | None,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    items = _load(brand_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    resolved_id = (chat_id or "").strip() or f"chat-{int(time.time() * 1000)}"
    first_user = next((m.get("text", "") for m in messages if m.get("role") == "user"), "")
    default_title = (first_user.strip().splitlines()[0][:56] if first_user.strip() else "Nova conversa")

    for entry in items:
        if entry.get("id") == resolved_id:
            if title:
                entry["title"] = title
            elif not entry.get("title"):
                entry["title"] = default_title
            entry["messages"] = messages
            entry["updatedAt"] = now_iso
            _save(brand_id, items)
            return entry

    entry = {
        "id": resolved_id,
        "brandId": brand_id,
        "title": title or default_title,
        "messages": messages,
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    items.insert(0, entry)
    _save(brand_id, items[:100])
    return entry


def delete_chat(brand_id: str, chat_id: str) -> bool:
    items = _load(brand_id)
    remaining = [c for c in items if c.get("id") != chat_id]
    if len(remaining) == len(items):
        return False
    _save(brand_id, remaining)
    return True
