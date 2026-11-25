from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from studio.brands import STUDIO_DIR

USAGE_FILE = STUDIO_DIR / "usage.json"

TRACKED_MODELS: list[tuple[str, str]] = [
    ("gemini-4-hydrogen", "Gemini 4 Hydrogen (Padrão)"),
    ("gemini-3-flash-preview", "Gemini 3 Flash"),
    ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite"),
    ("gemini-4-neon", "Gemini 4 Neon"),
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
]


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_usage() -> dict[str, Any]:
    if not USAGE_FILE.is_file():
        return {"date": _today_key(), "counts": {}}
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == _today_key():
            return data
    except Exception:
        pass
    return {"date": _today_key(), "counts": {}}


def record_usage(model_name: str) -> None:
    data = _load_usage()
    counts = data.setdefault("counts", {})
    counts[model_name] = int(counts.get(model_name, 0)) + 1
    USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _probe_model(client: httpx.AsyncClient, key: str, model_id: str, label: str, used_today: int) -> dict[str, Any]:
    try:
        res = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={key}",
            json={"contents": [{"parts": [{"text": "ok"}]}], "generationConfig": {"maxOutputTokens": 1}},
            timeout=15.0,
        )
        if res.status_code == 200:
            return {
                "id": model_id,
                "label": label,
                "status": "ok",
                "statusLabel": "Disponível",
                "detail": "Pronto para uso na sua chave",
                "usedToday": used_today,
            }
        err_msg = ""
        try:
            err_msg = str(res.json().get("error", {}).get("message") or "")
        except Exception:
            err_msg = res.text[:200]

        if res.status_code == 429:
            if "limit: 0" in err_msg:
                return {
                    "id": model_id,
                    "label": label,
                    "status": "no_tier",
                    "statusLabel": "Sem cota (limit: 0)",
                    "detail": "Modelo indisponível no plano Free Tier — requer billing no AI Studio",
                    "usedToday": used_today,
                }
            retry = re.search(r"retry in ([^\n.,]+)", err_msg, re.IGNORECASE)
            detail = f"Cota temporária atingida (tente em {retry.group(1)})" if retry else "Limite temporário atingido"
            return {
                "id": model_id,
                "label": label,
                "status": "limited",
                "statusLabel": "Limite atingido",
                "detail": detail,
                "usedToday": used_today,
            }
        return {
            "id": model_id,
            "label": label,
            "status": "error",
            "statusLabel": f"HTTP {res.status_code}",
            "detail": err_msg[:120],
            "usedToday": used_today,
        }
    except Exception as exc:
        return {
            "id": model_id,
            "label": label,
            "status": "error",
            "statusLabel": "Erro",
            "detail": str(exc)[:120],
            "usedToday": used_today,
        }


async def check_quota_status() -> dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    usage = _load_usage()
    counts = usage.get("counts", {})
    if not key:
        return {
            "hasKey": False,
            "date": usage["date"],
            "models": [],
            "error": "GEMINI_API_KEY não configurada em ⚙ → IA & R2.",
        }
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[
                _probe_model(client, key, model_id, label, int(counts.get(model_id, 0)))
                for model_id, label in TRACKED_MODELS
            ]
        )
    return {
        "hasKey": True,
        "date": usage["date"],
        "totalRequestsToday": sum(int(v) for v in counts.values()),
        "models": list(results),
    }
