from __future__ import annotations

import asyncio
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import httpx
from PIL import Image

from studio.brands import STUDIO_DIR, BrandConfig, get_brand, resolve_post_folder

GRAPH_BASE = "https://graph.facebook.com/v22.0"
SCHEDULES_FILE = STUDIO_DIR / "schedules.json"
HISTORY_FILE = STUDIO_DIR / "history.json"


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_json_list(path: Path, items: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_history(brand_id: str) -> list[dict[str, Any]]:
    return [h for h in _load_json_list(HISTORY_FILE) if h.get("brandId") == brand_id]


def list_schedules(brand_id: str) -> list[dict[str, Any]]:
    return [s for s in _load_json_list(SCHEDULES_FILE) if s.get("brandId") == brand_id]


def add_schedule(
    brand_id: str,
    post_id: str,
    post_type: str,
    caption: str,
    slides: list[str],
    scheduled_at_iso: str,
) -> dict[str, Any]:
    schedules = _load_json_list(SCHEDULES_FILE)
    entry = {
        "id": f"sch-{int(time.time() * 1000)}",
        "brandId": brand_id,
        "postId": post_id,
        "type": post_type,
        "caption": caption,
        "slides": slides,
        "scheduledAt": scheduled_at_iso,
        "status": "pending",
    }
    schedules.append(entry)
    _save_json_list(SCHEDULES_FILE, schedules)
    return entry


def update_schedule(
    schedule_id: str,
    post_type: str,
    caption: str,
    slides: list[str],
    scheduled_at_iso: str,
) -> dict[str, Any] | None:
    schedules = _load_json_list(SCHEDULES_FILE)
    for entry in schedules:
        if entry.get("id") == schedule_id:
            entry["type"] = post_type
            entry["caption"] = caption
            entry["slides"] = slides
            entry["scheduledAt"] = scheduled_at_iso
            entry["status"] = "pending"
            entry.pop("error", None)
            _save_json_list(SCHEDULES_FILE, schedules)
            return entry
    return None


def delete_schedule(schedule_id: str) -> bool:
    schedules = _load_json_list(SCHEDULES_FILE)
    remaining = [s for s in schedules if s.get("id") != schedule_id]
    if len(remaining) == len(schedules):
        return False
    _save_json_list(SCHEDULES_FILE, remaining)
    return True


def _get_r2_client() -> tuple[Any, str, str]:
    endpoint = os.getenv("R2_CLOUDFLARE_ENDPOINT_DEFAULT", "").rstrip("/")
    public_url = os.getenv("R2_CLOUDFLARE_PUBLIC_URL", "").rstrip("/")
    bucket = os.getenv("R2_BUCKET", "").strip() or "social-studio-media"
    access_key = os.getenv("R2_CLOUDFLARE_ACCESS_KEY_ID", "")
    secret_key = os.getenv("R2_CLOUDFLARE_SECRET_ACCESS_KEY", "")
    if not all([endpoint, public_url, access_key, secret_key]):
        raise RuntimeError("Credenciais R2_CLOUDFLARE_* ausentes no .env do Social Studio.")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )
    return client, bucket, public_url


def _upload_temp_jpeg(file_path: Path, index: int) -> tuple[str, str]:
    client, bucket, public_url = _get_r2_client()
    with Image.open(file_path) as img:
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=92, optimize=True)
        jpeg_bytes = buf.getvalue()
    key = f"tmp/social-studio/{int(time.time() * 1000)}-{index}.jpg"
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=jpeg_bytes,
        ContentType="image/jpeg",
        CacheControl="public, max-age=3600",
    )
    return key, f"{public_url}/{key}"


def _delete_temp_keys(keys: list[str]) -> None:
    if not keys:
        return
    try:
        client, bucket, _ = _get_r2_client()
        for key in keys:
            client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass


async def _graph_request(
    client: httpx.AsyncClient,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.request(
        method,
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30.0,
    )
    data = response.json()
    if response.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Meta Graph API ({response.status_code}): {data}")
    return data


async def _wait_container(client: httpx.AsyncClient, token: str, container_id: str) -> None:
    for _ in range(25):
        data = await _graph_request(client, token, "GET", f"/{container_id}?fields=status_code")
        status_code = data.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise RuntimeError(f"Container {container_id} falhou no processamento do Instagram.")
        await asyncio.sleep(1.5)
    raise RuntimeError(f"Timeout aguardando container {container_id}.")


async def check_instagram_profile(brand: BrandConfig) -> dict[str, Any] | None:
    if not brand.is_connected:
        return None
    try:
        async with httpx.AsyncClient() as client:
            return await _graph_request(
                client,
                brand.access_token,
                "GET",
                f"/{brand.account_id}?fields=id,username,name,followers_count,media_count",
            )
    except Exception as exc:
        return {"error": str(exc)}


async def publish_post(
    brand_id: str,
    post_id: str,
    post_type: str,
    caption: str,
    slides: list[str],
) -> dict[str, Any]:
    brand = get_brand(brand_id)
    if not brand.is_connected:
        raise RuntimeError(f"Conta do Instagram para {brand.name} não configurada no .env.")

    folder = resolve_post_folder(brand_id, post_id)
    file_paths = [folder / name for name in slides if (folder / name).is_file()]
    if not file_paths:
        raise RuntimeError("Nenhuma imagem encontrada para publicar.")

    uploaded_keys: list[str] = []
    public_urls: list[str] = []
    try:
        for idx, file_path in enumerate(file_paths):
            key, url = await asyncio.to_thread(_upload_temp_jpeg, file_path, idx)
            uploaded_keys.append(key)
            public_urls.append(url)

        async with httpx.AsyncClient() as client:
            if post_type == "story":
                last_published: dict[str, Any] = {}
                for url in public_urls:
                    container = await _graph_request(
                        client,
                        brand.access_token,
                        "POST",
                        f"/{brand.account_id}/media",
                        {"image_url": url, "media_type": "STORIES"},
                    )
                    await _wait_container(client, brand.access_token, container["id"])
                    last_published = await _graph_request(
                        client,
                        brand.access_token,
                        "POST",
                        f"/{brand.account_id}/media_publish",
                        {"creation_id": container["id"]},
                    )
                published = last_published
            elif post_type == "carousel" and len(public_urls) >= 2:
                child_ids: list[str] = []
                for url in public_urls[:10]:
                    child = await _graph_request(
                        client,
                        brand.access_token,
                        "POST",
                        f"/{brand.account_id}/media",
                        {"image_url": url, "is_carousel_item": True},
                    )
                    await _wait_container(client, brand.access_token, child["id"])
                    child_ids.append(child["id"])
                parent_payload: dict[str, Any] = {
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                }
                if caption.strip():
                    parent_payload["caption"] = caption.strip()
                parent = await _graph_request(
                    client,
                    brand.access_token,
                    "POST",
                    f"/{brand.account_id}/media",
                    parent_payload,
                )
                await _wait_container(client, brand.access_token, parent["id"])
                published = await _graph_request(
                    client,
                    brand.access_token,
                    "POST",
                    f"/{brand.account_id}/media_publish",
                    {"creation_id": parent["id"]},
                )
            else:
                payload: dict[str, Any] = {"image_url": public_urls[0]}
                if caption.strip():
                    payload["caption"] = caption.strip()
                container = await _graph_request(
                    client,
                    brand.access_token,
                    "POST",
                    f"/{brand.account_id}/media",
                    payload,
                )
                await _wait_container(client, brand.access_token, container["id"])
                published = await _graph_request(
                    client,
                    brand.access_token,
                    "POST",
                    f"/{brand.account_id}/media_publish",
                    {"creation_id": container["id"]},
                )
            try:
                details = await _graph_request(
                    client,
                    brand.access_token,
                    "GET",
                    f"/{published['id']}?fields=id,permalink",
                )
            except Exception:
                details = {"id": published["id"]}

        record = {
            "id": details.get("id", published["id"]),
            "permalink": details.get("permalink", ""),
            "brandId": brand_id,
            "postId": post_id,
            "type": post_type,
            "caption": caption,
            "publishedAt": datetime.now(timezone.utc).isoformat(),
        }
        history = _load_json_list(HISTORY_FILE)
        history.insert(0, record)
        _save_json_list(HISTORY_FILE, history[:100])
        return record
    finally:
        await asyncio.to_thread(_delete_temp_keys, uploaded_keys)


async def run_scheduler_tick() -> None:
    schedules = _load_json_list(SCHEDULES_FILE)
    now = datetime.now(timezone.utc)
    changed = False
    for entry in schedules:
        if entry.get("status") != "pending":
            continue
        try:
            due = datetime.fromisoformat(entry["scheduledAt"])
            if due.tzinfo is None:
                due = due.astimezone()
            due_utc = due.astimezone(timezone.utc)
        except Exception:
            continue
        if due_utc <= now:
            changed = True
            try:
                result = await publish_post(
                    entry["brandId"],
                    entry["postId"],
                    entry["type"],
                    entry.get("caption", ""),
                    entry.get("slides", []),
                )
                entry["status"] = "published"
                entry["permalink"] = result.get("permalink", "")
            except Exception as exc:
                entry["status"] = "failed"
                entry["error"] = str(exc)
    if changed:
        _save_json_list(SCHEDULES_FILE, schedules)
