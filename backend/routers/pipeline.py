# backend/routers/pipeline.py
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from services import service_manager as sm
from services import session_store

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post("/run/{step}/{sid}")
def run_step(step: str, sid: str) -> Dict[str, Any]:
    step = step.upper()
    if step not in sm.MV_FILES:
        raise HTTPException(400, f"unknown step: {step}")
    return sm.run_pipeline_step(sid, step)


@router.get("/status/{sid}")
def status(sid: str) -> Dict[str, Any]:
    try:
        s = session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    return {
        "pipeline_state": s["pipeline_state"],
        "gate_status":    s["gate"]["gate_status"],
        "mv_outputs":     list(s["mv_outputs"].keys()),
    }


@router.get("/output/{sid}/{step}")
def output(sid: str, step: str) -> Dict[str, Any]:
    try:
        s = session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    step = step.upper()
    out = s["mv_outputs"].get(step)
    if out is None:
        raise HTTPException(404, "output not ready")
    return {"step": step, "result": out}


@router.post("/preview/{sid}")
def preview(sid: str) -> Dict[str, Any]:
    """大白话讲解即将进行的影像制作流程"""
    try:
        s = session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    text = sm.memorial_preview(s["form_data"], s["mv_outputs"].get("MV01"))
    return {"text": text}


@router.post("/run-all/{sid}")
def run_all(sid: str) -> Dict[str, Any]:
    """串行运行 MV01→MV02→MV03，返回两段大白话气泡 + 分镜列表"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    return sm.run_pipeline_chain(sid)


@router.post("/scene/image/{sid}/{idx}")
def scene_image(sid: str, idx: int) -> Dict[str, Any]:
    """为单个分镜生成首帧图片（返回 data URL）"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    return sm.gen_scene_image(sid, idx)


@router.get("/characters/{sid}")
def characters(sid: str) -> Dict[str, Any]:
    """返回主角 + 配角档案"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    return sm.get_characters(sid)


@router.get("/scenes/{sid}")
def scenes(sid: str) -> Dict[str, Any]:
    """返回 MV04 已生成的分镜列表（含已渲染的图片/视频缓存）"""
    try:
        s = session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    mv04 = s["mv_outputs"].get("MV04")
    return {"scenes": sm._get_scenes_from_mv04(mv04), "ready": mv04 is not None}


@router.post("/scene/video/{sid}/{idx}")
def scene_video(sid: str, idx: int, payload: Optional[Dict[str, Any]] = Body(None)) -> Dict[str, Any]:
    """为单个分镜提交视频生成任务，立即返回 task_id（异步，不阻塞）"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    image_url = (payload or {}).get("image_url", "")
    return sm.gen_scene_video(sid, idx, image_url)


@router.get("/scene/video/status/{sid}/{idx}/{task_id}")
def scene_video_status(sid: str, idx: int, task_id: str, source: str = "302ai") -> Dict[str, Any]:
    """轮询视频任务状态。完成后自动下载到本地并返回 URL。"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")
    return sm.poll_scene_video(task_id, source, sid, idx)

