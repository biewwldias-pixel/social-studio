from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

# Quando empacotado com PyInstaller (--onefile), assets ficam em _MEIPASS e dados graváveis ao lado do .exe.
IS_FROZEN = getattr(sys, "frozen", False)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
STUDIO_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent.parent
PROJECTS_DIR = STUDIO_DIR.parent
DRAFTS_DIR = STUDIO_DIR / "drafts"
BRANDS_DIR = STUDIO_DIR / "brands"
BRANDS_FILE = STUDIO_DIR / "brands.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
COMMON_POST_DIRS = (
    "instagram-posts",
    "social-posts",
    "posts",
    "instagram",
    "social",
)


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")
    return cleaned or "perfil"


@dataclass
class BrandConfig:
    id: str
    name: str
    accent: str = "#6366f1"
    font_family: str = ""
    project_path_str: str = ""
    posts_dir_str: str = ""
    account_id_val: str = ""
    access_token_val: str = ""
    env_prefix: str = ""

    @property
    def project_path(self) -> Path:
        if self.project_path_str.strip():
            return Path(self.project_path_str.strip())
        if self.env_prefix:
            override = os.getenv(f"{self.env_prefix}_PROJECT_PATH", "").strip()
            if override:
                return Path(override)
        return PROJECTS_DIR / f"{self.id} project"

    @property
    def posts_dir(self) -> Path:
        if self.posts_dir_str.strip():
            p = Path(self.posts_dir_str.strip())
            return p if p.is_absolute() else (self.project_path / p)
        if self.project_path.is_dir():
            for candidate in COMMON_POST_DIRS:
                target = self.project_path / candidate
                if target.is_dir():
                    return target
        return self.project_path / "instagram-posts"

    @property
    def brand_doc(self) -> Path:
        local_doc = BRANDS_DIR / f"{self.id}.md"
        if local_doc.is_file():
            return local_doc
        bundled = BUNDLE_DIR / "brands" / f"{self.id}.md"
        if bundled.is_file():
            return bundled
        return local_doc

    @property
    def account_id(self) -> str:
        if self.account_id_val.strip():
            return self.account_id_val.strip()
        if self.env_prefix:
            return os.getenv(f"{self.env_prefix}_INSTAGRAM_ACCOUNT_ID", "").strip()
        return ""

    @property
    def access_token(self) -> str:
        if self.access_token_val.strip():
            return self.access_token_val.strip()
        if self.env_prefix:
            return os.getenv(f"{self.env_prefix}_INSTAGRAM_ACCESS_TOKEN", "").strip()
        return ""

    @property
    def is_connected(self) -> bool:
        return bool(self.account_id and self.access_token)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "accent": self.accent,
            "fontFamily": self.font_family,
            "projectPath": self.project_path_str,
            "postsDir": self.posts_dir_str,
            "accountId": self.account_id_val,
            "accessToken": self.access_token_val,
            "envPrefix": self.env_prefix,
        }


def _default_brands_data() -> list[dict[str, Any]]:
    return [
        {
            "id": "meu-projeto",
            "name": "Meu Projeto",
            "accent": "#6366f1",
            "fontFamily": "",
            "projectPath": "",
            "postsDir": "",
            "accountId": "",
            "accessToken": "",
            "envPrefix": "",
        },
    ]


def _ensure_brands_file() -> list[dict[str, Any]]:
    if not BRANDS_FILE.is_file():
        data = _default_brands_data()
        _save_brands_data(data)
        return data
    try:
        raw = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw:
            return raw
    except Exception:
        pass
    data = _default_brands_data()
    _save_brands_data(data)
    return data


def _save_brands_data(items: list[dict[str, Any]]) -> None:
    BRANDS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(BRANDS_FILE, 0o600)
    except OSError:
        pass


def _from_dict(item: dict[str, Any]) -> BrandConfig:
    return BrandConfig(
        id=str(item["id"]),
        name=str(item.get("name") or item["id"]),
        accent=str(item.get("accent") or "#6366f1"),
        font_family=str(item.get("fontFamily") or ""),
        project_path_str=str(item.get("projectPath") or ""),
        posts_dir_str=str(item.get("postsDir") or ""),
        account_id_val=str(item.get("accountId") or ""),
        access_token_val=str(item.get("accessToken") or ""),
        env_prefix=str(item.get("envPrefix") or ""),
    )


def list_brands() -> list[BrandConfig]:
    return [_from_dict(item) for item in _ensure_brands_file()]


def get_brand(brand_id: str) -> BrandConfig:
    for b in list_brands():
        if b.id == brand_id:
            return b
    raise KeyError(f"Marca desconhecida: {brand_id}")


def create_brand(
    *,
    name: str,
    project_path: str = "",
    posts_dir: str = "",
    account_id: str = "",
    access_token: str = "",
) -> BrandConfig:
    items = _ensure_brands_file()
    existing_ids = {item["id"] for item in items}
    base_id = slugify(name)
    brand_id = base_id
    counter = 2
    while brand_id in existing_ids:
        brand_id = f"{base_id}-{counter}"
        counter += 1
    entry = {
        "id": brand_id,
        "name": name.strip() or brand_id.title(),
        "accent": "#6366f1",
        "projectPath": project_path.strip(),
        "postsDir": posts_dir.strip(),
        "accountId": account_id.strip(),
        "accessToken": access_token.strip(),
        "envPrefix": "",
    }
    items.append(entry)
    _save_brands_data(items)
    return _from_dict(entry)


def update_brand(
    brand_id: str,
    *,
    name: str | None = None,
    accent: str | None = None,
    font_family: str | None = None,
    project_path: str | None = None,
    posts_dir: str | None = None,
    account_id: str | None = None,
    access_token: str | None = None,
) -> BrandConfig:
    items = _ensure_brands_file()
    for item in items:
        if item["id"] == brand_id:
            if name is not None and name.strip():
                item["name"] = name.strip()
            if accent is not None and accent.strip():
                item["accent"] = accent.strip()
            if font_family is not None:
                item["fontFamily"] = font_family.strip()
            if project_path is not None:
                item["projectPath"] = project_path.strip()
            if posts_dir is not None:
                item["postsDir"] = posts_dir.strip()
            if account_id is not None:
                item["accountId"] = account_id.strip()
            if access_token is not None:
                item["accessToken"] = access_token.strip()
            _save_brands_data(items)
            return _from_dict(item)
    raise KeyError(f"Marca desconhecida: {brand_id}")


def delete_brand(brand_id: str) -> bool:
    items = _ensure_brands_file()
    if len(items) <= 1:
        raise ValueError("Mantenha pelo menos 1 perfil cadastrado.")
    remaining = [item for item in items if item["id"] != brand_id]
    if len(remaining) == len(items):
        return False
    _save_brands_data(remaining)
    return True


def read_brand_guide(brand_id: str) -> str:
    brand = get_brand(brand_id)
    if brand.brand_doc.is_file():
        return brand.brand_doc.read_text(encoding="utf-8")
    return ""


def save_brand_guide(brand_id: str, content: str) -> Path:
    get_brand(brand_id)
    BRANDS_DIR.mkdir(parents=True, exist_ok=True)
    target = BRANDS_DIR / f"{brand_id}.md"
    target.write_text(content, encoding="utf-8")
    return target


def _detect_dimensions_and_type(folder: Path, images: list[str]) -> tuple[int, int, str, str]:
    width, height = 1080, 1350
    try:
        with Image.open(folder / images[0]) as img:
            width, height = img.size
    except Exception:
        pass
    ratio = height / width if width > 0 else 1.25
    if ratio >= 1.55:
        return width, height, "9:16", "story"
    aspect = "1:1" if 0.95 <= ratio <= 1.05 else ("4:5" if ratio > 1.05 else "16:9")
    detected_type = "carousel" if len(images) > 1 else "feed"
    return width, height, aspect, detected_type


def _scan_post_folder(folder: Path, source: str, brand_id: str) -> dict[str, Any] | None:
    images = sorted(
        p.name
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTENSIONS
        and not p.name.startswith("logo-")
    )
    if not images:
        return None
    meta_path = folder / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    width, height, aspect, detected_type = _detect_dimensions_and_type(folder, images)
    mtimes = [folder.stat().st_mtime]
    if meta_path.is_file():
        mtimes.append(meta_path.stat().st_mtime)
    for img_name in images:
        try:
            mtimes.append((folder / img_name).stat().st_mtime)
        except OSError:
            pass
    return {
        "id": f"{source}:{folder.name}",
        "slug": folder.name,
        "source": source,
        "brandId": brand_id,
        "title": meta.get("title") or folder.name.replace("-", " ").title(),
        "type": meta.get("type") or detected_type,
        "detectedType": detected_type,
        "width": width,
        "height": height,
        "aspect": aspect,
        "caption": meta.get("caption") or "",
        "slides": images,
        "updatedAt": int(max(mtimes)),
    }


def list_brand_posts(brand_id: str) -> list[dict[str, Any]]:
    brand = get_brand(brand_id)
    results: list[dict[str, Any]] = []
    for source, root in [("drafts", DRAFTS_DIR / brand_id), ("project", brand.posts_dir)]:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            item = _scan_post_folder(entry, source, brand_id)
            if item:
                results.append(item)
    results.sort(key=lambda item: item["updatedAt"], reverse=True)
    return results


def resolve_post_folder(brand_id: str, post_id: str) -> Path:
    brand = get_brand(brand_id)
    if ":" not in post_id:
        raise ValueError("post_id inválido")
    source, slug = post_id.split(":", 1)
    if "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError("slug inválido")
    base = (DRAFTS_DIR / brand_id) if source == "drafts" else brand.posts_dir
    folder = (base / slug).resolve()
    if not folder.is_dir() or base.resolve() not in folder.parents:
        raise FileNotFoundError(f"Post não encontrado: {post_id}")
    return folder


def save_post_meta(brand_id: str, post_id: str, *, caption: str, post_type: str) -> None:
    folder = resolve_post_folder(brand_id, post_id)
    meta_path = folder / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    meta["caption"] = caption
    meta["type"] = post_type
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def duplicate_post(brand_id: str, post_id: str) -> dict[str, Any]:
    source, slug = post_id.split(":", 1)
    src_folder = resolve_post_folder(brand_id, post_id)
    parent = src_folder.parent
    candidate_slug = f"{slug}-copia"
    counter = 2
    while (parent / candidate_slug).exists():
        candidate_slug = f"{slug}-copia-{counter}"
        counter += 1
    dst_folder = parent / candidate_slug
    shutil.copytree(src_folder, dst_folder)

    meta_path = dst_folder / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    base_title = meta.get("title") or slug.replace("-", " ").title()
    meta["title"] = f"{base_title} (Cópia)"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    item = _scan_post_folder(dst_folder, source, brand_id)
    if not item:
        raise RuntimeError("Falha ao duplicar o modelo.")
    return item


def delete_post(brand_id: str, post_id: str) -> None:
    folder = resolve_post_folder(brand_id, post_id)
    shutil.rmtree(folder)
