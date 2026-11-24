from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv, set_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from studio.agent_runner import BrandAgentPool
from studio.brands import (
    BUNDLE_DIR,
    STUDIO_DIR,
    create_brand,
    delete_brand,
    delete_post,
    duplicate_post,
    get_brand,
    list_brand_posts,
    list_brands,
    read_brand_guide,
    resolve_post_folder,
    save_brand_guide,
    save_post_meta,
    update_brand,
)

ENV_PATH = STUDIO_DIR / ".env"
load_dotenv(ENV_PATH)

ALLOWED_ENV_KEYS = (
    "GEMINI_API_KEY",
    "R2_CLOUDFLARE_ACCESS_KEY_ID",
    "R2_CLOUDFLARE_SECRET_ACCESS_KEY",
    "R2_CLOUDFLARE_ENDPOINT_DEFAULT",
    "R2_CLOUDFLARE_PUBLIC_URL",
    "R2_BUCKET",
)
from studio.chats import delete_chat, list_chats, upsert_chat
from studio.publisher import (
    add_schedule,
    check_instagram_profile,
    delete_schedule,
    list_history,
    list_schedules,
    publish_post,
    run_scheduler_tick,
    update_schedule,
)

agent_pool = BrandAgentPool()


async def _scheduler_loop() -> None:
    while True:
        try:
            await run_scheduler_tick()
        except Exception:
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler_task = asyncio.create_task(_scheduler_loop())
    try:
        yield
    finally:
        scheduler_task.cancel()
        await agent_pool.shutdown()


app = FastAPI(title="Social Studio", lifespan=lifespan)


class ChatRequest(BaseModel):
    brandId: str
    message: str
    model: str | None = None
    thinking: str | None = None


class SettingsRequest(BaseModel):
    values: dict[str, str]


class PickFolderRequest(BaseModel):
    initialDir: str | None = None


class CreateBrandRequest(BaseModel):
    name: str
    projectPath: str = ""
    postsDir: str = ""
    accountId: str = ""
    accessToken: str = ""


class UpdateBrandRequest(BaseModel):
    name: str | None = None
    accent: str | None = None
    fontFamily: str | None = None
    projectPath: str | None = None
    postsDir: str | None = None
    accountId: str | None = None
    accessToken: str | None = None
    guide: str | None = None


class AnalyzeDesignRequest(BaseModel):
    model: str | None = "gemini-4-neon"
    thinking: str | None = "medium"


class SaveChatRequest(BaseModel):
    id: str | None = None
    title: str | None = None
    messages: list[dict[str, Any]]


class SaveMetaRequest(BaseModel):
    brandId: str
    postId: str
    caption: str
    type: str


class PostActionRequest(BaseModel):
    brandId: str
    postId: str


class PublishRequest(BaseModel):
    brandId: str
    postId: str
    type: str
    caption: str
    slides: list[str]


class ScheduleRequest(PublishRequest):
    scheduledAt: str


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    html = (BUNDLE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/guia", response_class=HTMLResponse)
async def guide_page() -> HTMLResponse:
    html = (BUNDLE_DIR / "static" / "guia.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/icon.svg")
async def icon_svg() -> FileResponse:
    return FileResponse(BUNDLE_DIR / "static" / "icon.svg", media_type="image/svg+xml")


@app.get("/icon-512.png")
async def icon_512() -> FileResponse:
    return FileResponse(BUNDLE_DIR / "static" / "icon-512.png", media_type="image/png")


@app.get("/icon-192.png")
async def icon_192() -> FileResponse:
    return FileResponse(BUNDLE_DIR / "static" / "icon-192.png", media_type="image/png")


@app.get("/manifest.json")
async def manifest_json() -> FileResponse:
    return FileResponse(BUNDLE_DIR / "static" / "manifest.json", media_type="application/manifest+json")


@app.get("/api/brands")
async def api_brands() -> list[dict[str, Any]]:
    result = []
    for brand in list_brands():
        profile = await check_instagram_profile(brand)
        guide_text = read_brand_guide(brand.id)
        result.append(
            {
                "id": brand.id,
                "name": brand.name,
                "accent": brand.accent,
                "fontFamily": brand.font_family,
                "projectPath": brand.project_path_str or str(brand.project_path),
                "postsDir": brand.posts_dir_str,
                "resolvedPostsDir": str(brand.posts_dir),
                "accountId": brand.account_id,
                "accessToken": brand.access_token,
                "isConnected": brand.is_connected,
                "hasGuide": bool(guide_text.strip()),
                "guidePath": str(brand.brand_doc),
                "profile": profile,
            }
        )
    return result


@app.post("/api/brands")
async def api_create_brand(body: CreateBrandRequest) -> dict[str, Any]:
    brand = create_brand(
        name=body.name,
        project_path=body.projectPath,
        posts_dir=body.postsDir,
        account_id=body.accountId,
        access_token=body.accessToken,
    )
    return {"id": brand.id, "name": brand.name}


@app.put("/api/brands/{brand_id}")
async def api_update_brand(brand_id: str, body: UpdateBrandRequest) -> dict[str, Any]:
    try:
        brand = update_brand(
            brand_id,
            name=body.name,
            accent=body.accent,
            font_family=body.fontFamily,
            project_path=body.projectPath,
            posts_dir=body.postsDir,
            account_id=body.accountId,
            access_token=body.accessToken,
        )
        if body.guide is not None:
            save_brand_guide(brand_id, body.guide)
        await agent_pool.reset(brand_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": brand.id, "name": brand.name}


@app.delete("/api/brands/{brand_id}")
async def api_delete_brand(brand_id: str) -> dict[str, str]:
    try:
        deleted = delete_brand(brand_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Perfil não encontrado")
    await agent_pool.reset(brand_id)
    return {"status": "deleted"}


@app.get("/api/brands/{brand_id}/guide")
async def api_get_brand_guide(brand_id: str) -> dict[str, str]:
    try:
        brand = get_brand(brand_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"content": read_brand_guide(brand_id), "path": str(brand.brand_doc)}


@app.post("/api/brands/{brand_id}/analyze-design")
async def api_analyze_design(brand_id: str, body: AnalyzeDesignRequest) -> StreamingResponse:
    async def event_stream():
        async for event in agent_pool.stream_analyze_design(
            brand_id, model=body.model, thinking=body.thinking
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/brands/{brand_id}/posts")
async def api_brand_posts(brand_id: str) -> dict[str, Any]:
    try:
        get_brand(brand_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "posts": list_brand_posts(brand_id),
        "schedules": list_schedules(brand_id),
        "history": list_history(brand_id),
    }


@app.get("/api/brands/{brand_id}/image/{post_id}/{filename}")
async def api_post_image(brand_id: str, post_id: str, filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")
    try:
        folder = resolve_post_folder(brand_id, post_id)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    target = (folder / filename).resolve()
    if not target.is_file() or folder not in target.parents:
        raise HTTPException(status_code=404, detail="Imagem não encontrada")
    return FileResponse(target)


@app.post("/api/posts/meta")
async def api_save_meta(body: SaveMetaRequest) -> dict[str, str]:
    try:
        save_post_meta(body.brandId, body.postId, caption=body.caption, post_type=body.type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/api/posts/duplicate")
async def api_duplicate_post(body: PostActionRequest) -> dict[str, Any]:
    try:
        return duplicate_post(body.brandId, body.postId)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/brands/{brand_id}/posts/{post_id:path}")
async def api_delete_post(brand_id: str, post_id: str) -> dict[str, str]:
    try:
        delete_post(brand_id, post_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted"}


@app.get("/api/brands/{brand_id}/posts/{post_id:path}/zip")
async def api_download_post_zip(brand_id: str, post_id: str) -> Response:
    try:
        folder = resolve_post_folder(brand_id, post_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and not file.name.startswith("logo-"):
                zf.write(file, arcname=f"{folder.name}/{file.name}")
        meta_path = folder / "meta.json"
        if meta_path.is_file():
            try:
                caption = json.loads(meta_path.read_text(encoding="utf-8")).get("caption", "")
                if caption:
                    zf.writestr(f"{folder.name}/legenda.txt", caption)
            except Exception:
                pass
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{folder.name}.zip"'},
    )


@app.post("/api/publish")
async def api_publish(body: PublishRequest) -> dict[str, Any]:
    try:
        save_post_meta(body.brandId, body.postId, caption=body.caption, post_type=body.type)
        return await publish_post(body.brandId, body.postId, body.type, body.caption, body.slides)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/schedule")
async def api_schedule(body: ScheduleRequest) -> dict[str, Any]:
    try:
        save_post_meta(body.brandId, body.postId, caption=body.caption, post_type=body.type)
        return add_schedule(
            body.brandId, body.postId, body.type, body.caption, body.slides, body.scheduledAt
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/schedule/{schedule_id}")
async def api_update_schedule(schedule_id: str, body: ScheduleRequest) -> dict[str, Any]:
    try:
        save_post_meta(body.brandId, body.postId, caption=body.caption, post_type=body.type)
    except Exception:
        pass
    updated = update_schedule(
        schedule_id, body.type, body.caption, body.slides, body.scheduledAt
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return updated


@app.delete("/api/schedule/{schedule_id}")
async def api_delete_schedule(schedule_id: str) -> dict[str, str]:
    if not delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return {"status": "deleted"}


@app.get("/api/brands/{brand_id}/chats")
async def api_list_chats(brand_id: str) -> list[dict[str, Any]]:
    return list_chats(brand_id)


@app.post("/api/brands/{brand_id}/chats")
async def api_save_chat(brand_id: str, body: SaveChatRequest) -> dict[str, Any]:
    return upsert_chat(
        brand_id,
        chat_id=body.id,
        title=body.title,
        messages=body.messages,
    )


@app.delete("/api/brands/{brand_id}/chats/{chat_id}")
async def api_delete_chat(brand_id: str, chat_id: str) -> dict[str, str]:
    if not delete_chat(brand_id, chat_id):
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return {"status": "deleted"}


@app.post("/api/chat/reset/{brand_id}")
async def api_chat_reset(brand_id: str) -> dict[str, str]:
    await agent_pool.reset(brand_id)
    return {"status": "reset"}


def _pick_folder_sync(initial_dir: str | None = None) -> str:
    start = initial_dir if initial_dir and Path(initial_dir).is_dir() else str(STUDIO_DIR.parent)
    if sys.platform.startswith("linux"):
        if shutil.which("zenity"):
            res = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    "--title=Escolher pasta do projeto",
                    f"--filename={start.rstrip('/')}/",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            return res.stdout.strip() if res.returncode == 0 else ""
        if shutil.which("kdialog"):
            res = subprocess.run(
                ["kdialog", "--getexistingdirectory", start, "--title", "Escolher pasta do projeto"],
                capture_output=True,
                text=True,
                check=False,
            )
            return res.stdout.strip() if res.returncode == 0 else ""
    elif sys.platform == "darwin":
        res = subprocess.run(
            ["osascript", "-e", 'POSIX path of (choose folder with prompt "Escolher pasta do projeto")'],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip() if res.returncode == 0 else ""
    elif sys.platform == "win32":
        escaped_start = start.replace('"', '`"')
        ps_script = (
            "Add-Type -TypeDefinition @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public static class ModernFolderPicker {\n"
            "  [ComImport, Guid(\"DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7\")] class FileOpenDialog {}\n"
            "  [ComImport, Guid(\"42f85136-db7e-439c-85f1-e4075d135fc8\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
            "  interface IFileDialog {\n"
            "    [PreserveSig] int Show(IntPtr parent);\n"
            "    void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);\n"
            "    void SetFileTypeIndex(uint iFileType);\n"
            "    void GetFileTypeIndex(out uint piFileType);\n"
            "    void Advise(IntPtr pfde, out uint pdwCookie);\n"
            "    void Unadvise(uint dwCookie);\n"
            "    void SetOptions(uint fos);\n"
            "    void GetOptions(out uint pfos);\n"
            "    void SetDefaultFolder(IShellItem psi);\n"
            "    void SetFolder(IShellItem psi);\n"
            "    void GetFolder(out IShellItem ppsi);\n"
            "    void GetCurrentSelection(out IShellItem ppsi);\n"
            "    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);\n"
            "    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);\n"
            "    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);\n"
            "    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);\n"
            "    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);\n"
            "    void GetResult(out IShellItem ppsi);\n"
            "  }\n"
            "  [ComImport, Guid(\"43826D1E-E718-42EE-BC55-A1E261C37BFE\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
            "  interface IShellItem {\n"
            "    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);\n"
            "    void GetParent(out IShellItem ppsi);\n"
            "    void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);\n"
            "  }\n"
            "  [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)]\n"
            "  static extern int SHCreateItemFromParsingName(string pszPath, IntPtr pbc, ref Guid riid, out IShellItem ppv);\n"
            "  public static string Pick(string initialDir) {\n"
            "    var dlg = (IFileDialog)new FileOpenDialog();\n"
            "    uint opts; dlg.GetOptions(out opts);\n"
            "    dlg.SetOptions(opts | 0x00000020 | 0x00000040);\n"
            "    dlg.SetTitle(\"Escolher pasta do projeto (Windows ou Linux/WSL)\");\n"
            "    if (!string.IsNullOrEmpty(initialDir)) {\n"
            "      var iid = typeof(IShellItem).GUID;\n"
            "      IShellItem item;\n"
            "      if (SHCreateItemFromParsingName(initialDir, IntPtr.Zero, ref iid, out item) == 0 && item != null)\n"
            "        dlg.SetFolder(item);\n"
            "    }\n"
            "    if (dlg.Show(IntPtr.Zero) == 0) {\n"
            "      IShellItem res; dlg.GetResult(out res);\n"
            "      string path; res.GetDisplayName(0x80058000, out path);\n"
            "      return path ?? \"\";\n"
            "    }\n"
            "    return \"\";\n"
            "  }\n"
            "}\n"
            "'@\n"
            f'[ModernFolderPicker]::Pick("{escaped_start}")'
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip() if res.returncode == 0 else ""

    tk_script = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = filedialog.askdirectory(initialdir=sys.argv[1], title='Escolher pasta do projeto') or ''\n"
        "root.destroy()\n"
        "print(path)\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", tk_script, start],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.strip() if res.returncode == 0 else ""


@app.post("/api/pick-folder")
async def api_pick_folder(body: PickFolderRequest) -> dict[str, str]:
    try:
        selected = await asyncio.to_thread(_pick_folder_sync, body.initialDir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Não foi possível abrir o seletor de pastas: {exc}") from exc
    return {"path": selected}


@app.get("/api/settings")
async def api_get_settings() -> dict[str, Any]:
    current = dotenv_values(ENV_PATH) if ENV_PATH.is_file() else {}
    return {
        "values": {
            key: (os.getenv(key) or current.get(key) or "") for key in ALLOWED_ENV_KEYS
        }
    }


@app.post("/api/settings")
async def api_save_settings(body: SettingsRequest) -> dict[str, str]:
    if not ENV_PATH.exists():
        ENV_PATH.touch(mode=0o600)
    for key in ALLOWED_ENV_KEYS:
        if key not in body.values:
            continue
        val = body.values[key].strip()
        set_key(str(ENV_PATH), key, val, quote_mode="never")
        os.environ[key] = val
    os.chmod(ENV_PATH, 0o600)
    return {"status": "saved"}


@app.post("/api/chat")
async def api_chat(body: ChatRequest) -> StreamingResponse:
    async def event_stream():
        async for event in agent_pool.stream_chat(
            body.brandId, body.message, model=body.model, thinking=body.thinking
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
