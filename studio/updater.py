from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from studio.brands import IS_FROZEN, STUDIO_DIR

APP_VERSION = "1.0.2"
GITHUB_REPO = "biewwldias-pixel/social-studio"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> tuple[int, ...]:
    cleaned = tag.strip().lstrip("vV")
    parts: list[int] = []
    for piece in cleaned.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


async def check_for_update() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            res = await client.get(
                LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "SocialStudio"},
            )
        if res.status_code == 404:
            return {
                "currentVersion": APP_VERSION,
                "latestVersion": APP_VERSION,
                "updateAvailable": False,
                "isFrozen": IS_FROZEN,
                "releaseUrl": f"https://github.com/{GITHUB_REPO}/releases",
                "downloadUrl": "",
                "notes": "",
            }
        res.raise_for_status()
        data = res.json()
        tag = str(data.get("tag_name") or APP_VERSION)
        latest = tag.lstrip("vV")
        download_url = ""
        for asset in data.get("assets") or []:
            name = str(asset.get("name") or "")
            if name.lower().endswith(".exe"):
                download_url = str(asset.get("browser_download_url") or "")
                break
        return {
            "currentVersion": APP_VERSION,
            "latestVersion": latest,
            "updateAvailable": _parse_version(latest) > _parse_version(APP_VERSION),
            "isFrozen": IS_FROZEN,
            "releaseUrl": str(data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"),
            "downloadUrl": download_url,
            "notes": str(data.get("body") or ""),
        }
    except Exception as exc:
        return {
            "currentVersion": APP_VERSION,
            "latestVersion": APP_VERSION,
            "updateAvailable": False,
            "isFrozen": IS_FROZEN,
            "error": str(exc),
        }


async def apply_update(download_url: str = "") -> dict[str, str]:
    if IS_FROZEN and sys.platform == "win32":
        if not download_url:
            info = await check_for_update()
            download_url = str(info.get("downloadUrl") or "")
        if not download_url:
            raise RuntimeError("Link de download do SocialStudio.exe não encontrado na Release.")

        current_exe = Path(sys.executable).resolve()
        new_exe = current_exe.with_name(current_exe.name + ".new")
        async with httpx.AsyncClient(follow_redirects=True, timeout=180.0) as client:
            async with client.stream("GET", download_url, headers={"User-Agent": "SocialStudio"}) as resp:
                resp.raise_for_status()
                with new_exe.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 256):
                        fh.write(chunk)

        updater_bat = current_exe.parent / "_update_social_studio.bat"
        bat_content = (
            "@echo off\r\n"
            "setlocal\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            ":retry\r\n"
            f'move /y "{new_exe}" "{current_exe}" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >nul\r\n"
            "  goto retry\r\n"
            ")\r\n"
            f'start "" "{current_exe}"\r\n'
            'del "%~f0"\r\n'
        )
        updater_bat.write_text(bat_content, encoding="utf-8")

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        subprocess.Popen(
            ["cmd", "/c", str(updater_bat)],
            creationflags=creation_flags,
            close_fds=True,
        )

        async def _exit_soon() -> None:
            await asyncio.sleep(0.8)
            os._exit(0)

        asyncio.create_task(_exit_soon())
        return {
            "status": "restarting",
            "message": "Atualização baixada! O Social Studio será reiniciado em instantes...",
        }

    # Modo código-fonte (Linux / macOS / dev): atualiza via git pull
    if (STUDIO_DIR / ".git").is_dir():
        res = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", str(STUDIO_DIR), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip() or "Falha ao executar git pull.")
        return {
            "status": "updated",
            "message": "Código atualizado via Git! Reinicie o app para aplicar.",
        }

    raise RuntimeError("Baixe a nova versão diretamente pela página de Releases do GitHub.")
