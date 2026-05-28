# backend/routers/comfyui.py
"""
ComfyUI LTX-2.3 视频生成路由 — 独立模块，可选挂载。

在 main.py 中添加：
    from routers.comfyui import router as comfyui_router
    app.include_router(comfyui_router)
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from services import session_store
from services.comfyui_video import (
    generate_video,
    generate_video_async,
    poll_video,
    download_video,
    health_check,
    generate_scene_video,
)

router = APIRouter(prefix="/comfyui", tags=["comfyui"])


@router.get("/health")
def comfyui_health() -> Dict[str, Any]:
    """检查 ComfyUI 服务器状态"""
    result = health_check()
    if not result.get("ok"):
        raise HTTPException(503, result.get("error", "ComfyUI 不可用"))
    return result


@router.post("/generate/{sid}/{scene_idx}")
def comfyui_for_scene(
    sid: str,
    scene_idx: int,
    payload: Optional[Dict[str, Any]] = Body(None),
) -> Dict[str, Any]:
    """
    为指定 session 的某个分镜生成视频（同步阻塞）。

    payload 可选字段：
      - seed:      随机种子
      - frames:    帧数
      - width:     宽度
      - height:    高度
      - max_wait:  最大等待秒数
    """
    try:
        s = session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")

    # 从 session 获取分镜信息
    mv04 = s["mv_outputs"].get("MV04")
    mv03 = s["mv_outputs"].get("MV03")
    from services.service_manager import _get_scenes_from_mv04
    scenes = _get_scenes_from_mv04(mv04)
    if scene_idx < 0 or scene_idx >= len(scenes):
        raise HTTPException(400, f"无效的分镜索引 {scene_idx}")

    scene = scenes[scene_idx]
    payload = payload or {}

    # 获取图片路径
    image_path = scene.get("_image_path") or ""
    if not image_path:
        raise HTTPException(400, "请先生成首帧图片")

    # 构造 prompt
    prompt = scene.get("prompt_video") or scene.get("description") or scene.get("visual", "")
    if not prompt:
        raise HTTPException(400, "分镜缺少视频描述")

    result = generate_video(
        prompt=prompt,
        image_path=image_path,
        seed=payload.get("seed"),
        frames=payload.get("frames"),
        width=payload.get("width"),
        height=payload.get("height"),
        max_wait=payload.get("max_wait", 600),
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "视频生成失败"))
    return result


@router.post("/submit")
def comfyui_submit(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    异步提交视频生成任务，返回 prompt_id。

    必填：prompt (str)
    可选：image_path, negative_prompt, seed, frames, width, height, workflow_path
    """
    prompt = payload.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt 不能为空")

    result = generate_video_async(
        prompt=prompt,
        image_path=payload.get("image_path"),
        negative_prompt=payload.get("negative_prompt"),
        seed=payload.get("seed"),
        frames=payload.get("frames"),
        width=payload.get("width"),
        height=payload.get("height"),
        workflow_path=payload.get("workflow_path"),
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "提交失败"))
    return result


@router.get("/poll/{prompt_id}")
def comfyui_poll(prompt_id: str, max_wait: int = 600) -> Dict[str, Any]:
    """轮询视频生成任务状态（阻塞直到完成或超时）"""
    result = poll_video(prompt_id, max_wait=max_wait)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "轮询失败"))
    return result


@router.post("/download")
def comfyui_download(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """下载已生成的视频到本地"""
    filename = payload.get("filename", "")
    if not filename:
        raise HTTPException(400, "filename 不能为空")
    result = download_video(
        filename=filename,
        output_path=payload.get("output_path"),
        subfolder=payload.get("subfolder", ""),
        output_type=payload.get("output_type", "output"),
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "下载失败"))
    return result
