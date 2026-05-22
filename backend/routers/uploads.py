# backend/routers/uploads.py — 文件上传 + LLM 自动打标签
import os, mimetypes, json
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from openai import OpenAI
from core import security, storage
from logger import api_logger

router = APIRouter(prefix="/memorials", tags=["uploads"])


def _get_llm() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )


def _guess_kind(filename: str, mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("image/"): return "image"
    if m.startswith("audio/"): return "audio"
    if m.startswith("video/"): return "video"
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    if ext in ("txt","md","pdf","doc","docx","csv","json"): return "document"
    return "other"


def _auto_tag(filename: str, kind: str, description: str) -> dict:
    """调用 LLM 给文件打标签 + 推断可用场景。失败则降级为基础标签。"""
    fallback = {
        "tags": [kind],
        "usable_for": ["dossier"],
        "summary": description or filename,
    }
    if not os.getenv("DASHSCOPE_API_KEY"):
        return fallback
    try:
        client = _get_llm()
        prompt = f"""你是念念追思助手的资料管理 Agent。用户上传了一个文件，请输出 JSON：
{{
  "tags": [3-6个中文标签，刻画文件主题/年代/场景/情绪],
  "usable_for": ["person_profile" | "video_storyboard" | "biography_chapter" | "digital_human_voice" | "digital_human_memory" 中的若干],
  "summary": "20字以内中文摘要"
}}
不要输出任何 JSON 以外的内容。

文件名：{filename}
文件类型：{kind}
用户描述：{description or "(用户未填写)"}"""
        resp = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        txt = (resp.choices[0].message.content or "").strip()
        # 取出第一段 { ... }
        i, j = txt.find("{"), txt.rfind("}")
        if i >= 0 and j > i:
            return json.loads(txt[i:j+1])
    except Exception as e:
        api_logger.exception("[auto_tag] failed: %s", e)
    return fallback


@router.post("/{mid}/upload")
async def upload(
    mid: str,
    file: UploadFile = File(...),
    description: str = Form(""),
    user = Depends(security.get_current_user),
):
    uid = user["user_id"]
    meta = storage.get_memorial(uid, mid)
    if not meta:
        raise HTTPException(404, "未找到该纪念对象")

    raw = await file.read()
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(413, "单文件不超过 50MB")

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    kind = _guess_kind(file.filename or "", mime)
    ext = (file.filename or "file").rsplit(".", 1)[-1] if "." in (file.filename or "") else "bin"
    aid = storage.new_id("a_")
    save_name = f"{aid}.{ext}"
    save_path = storage.memorial_dir(uid, mid) / "assets" / save_name
    save_path.parent.mkdir(parents=True, exist_ok=True)
    storage.save_binary(save_path, raw)

    tag_info = _auto_tag(file.filename or "", kind, description)

    asset = {
        "asset_id": aid,
        "filename": file.filename,
        "stored_name": save_name,
        "mime": mime,
        "kind": kind,                 # image / audio / video / document / other
        "size": len(raw),
        "description": description,   # 用户对该文件的说明（"这是什么"）
        "tags": tag_info.get("tags", []),
        "usable_for": tag_info.get("usable_for", []),
        "summary": tag_info.get("summary", ""),
        "url": f"/api/memorials/{mid}/assets/{aid}",
        "created_at": storage.now_iso(),
    }
    storage.add_asset(uid, mid, asset)
    return {"asset": asset}


@router.get("/{mid}/assets")
def list_assets(mid: str, user = Depends(security.get_current_user)):
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "未找到")
    return {"assets": storage.list_assets(user["user_id"], mid)}


@router.get("/{mid}/assets/{aid}")
def get_asset_file(mid: str, aid: str, user = Depends(security.get_current_user)):
    uid = user["user_id"]
    assets = storage.list_assets(uid, mid)
    a = next((x for x in assets if x.get("asset_id") == aid), None)
    if not a:
        raise HTTPException(404, "文件不存在")
    p = storage.memorial_dir(uid, mid) / "assets" / a.get("stored_name", "")
    if not p.exists():
        raise HTTPException(404, "文件已删除")
    return FileResponse(str(p), media_type=a.get("mime", "application/octet-stream"), filename=a.get("filename") or a.get("stored_name"))


class AssetPatchReq(__import__("pydantic").BaseModel):
    description: str | None = None
    tags: list[str] | None = None
    usable_for: list[str] | None = None

@router.patch("/{mid}/assets/{aid}")
def patch_asset(mid: str, aid: str, req: AssetPatchReq, user = Depends(security.get_current_user)):
    patch = {k: v for k, v in req.dict().items() if v is not None}
    a = storage.update_asset(user["user_id"], mid, aid, patch)
    if not a:
        raise HTTPException(404, "文件不存在")
    return {"asset": a}
