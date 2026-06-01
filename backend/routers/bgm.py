# backend/routers/bgm.py
"""
Suno BGM 生成路由 — 独立模块，可选挂载。

在 main.py 中添加：
    from routers.bgm import router as bgm_router
    app.include_router(bgm_router)
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from services import session_store
from services.suno_bgm import (
    build_memorial_tags,
    generate_bgm,
    generate_bgm_async,
    poll_bgm,
    download_bgm,
    generate_memorial_bgm,
)

router = APIRouter(prefix="/bgm", tags=["bgm"])


@router.post("/generate/{sid}")
def bgm_for_session(sid: str, payload: Optional[Dict[str, Any]] = Body(None)) -> Dict[str, Any]:
    """
    为指定 session 生成追思 BGM（同步阻塞，可能需要 2-5 分钟）。

    payload 可选字段：
      - style:    风格，默认取 form_data.style_preference
      - keywords: 附加关键词列表
      - max_wait: 最大等待秒数，默认 300
    """
    try:
        s = session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")

    form = s.get("form_data", {})
    payload = payload or {}

    style = payload.get("style") or form.get("style_preference", "warm_nostalgia")
    keywords = payload.get("keywords")
    max_wait = payload.get("max_wait", 300)
    name = form.get("deceased_name", "逝者")

    result = generate_memorial_bgm(name, style, keywords, max_wait=max_wait)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "BGM 生成失败"))
    return result


@router.post("/submit")
def bgm_submit(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    异步提交 BGM 任务，返回 task_id。

    必填：tags (str)
    可选：title, make_instrumental, model
    """
    tags = payload.get("tags", "")
    if not tags:
        raise HTTPException(400, "tags 不能为空")

    result = generate_bgm_async(
        tags=tags,
        title=payload.get("title", "追思 BGM"),
        make_instrumental=payload.get("make_instrumental", True),
        model=payload.get("model", "chirp-v4"),
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "提交失败"))
    return result


@router.get("/poll/{task_id}")
def bgm_poll(task_id: str, max_wait: int = 600) -> Dict[str, Any]:
    """轮询 BGM 任务状态（阻塞直到完成或超时）"""
    result = poll_bgm(task_id, max_wait=max_wait)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "轮询失败"))
    return result


@router.post("/download")
def bgm_download(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """下载已生成的 BGM 到本地"""
    audio_url = payload.get("audio_url", "")
    if not audio_url:
        raise HTTPException(400, "audio_url 不能为空")
    output_path = payload.get("output_path")
    result = download_bgm(audio_url, output_path)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "下载失败"))
    return result


@router.get("/tags")
def bgm_tags(style: str = "warm_nostalgia", keywords: str = "") -> Dict[str, Any]:
    """
    预览 Suno tags（不实际生成，供前端调参用）。

    keywords 逗号分隔，如 "工程师,书法,太极拳"
    """
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
    tags = build_memorial_tags(style, kw_list)
    return {"tags": tags, "style": style, "keywords": kw_list or []}
