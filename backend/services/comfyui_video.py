# backend/services/comfyui_video.py
"""
ComfyUI LTX-2.3 Image-to-Video 模块 — 独立可调用。

通过 HTTP + 轮询方式调用远程 ComfyUI 服务器，无需 websocket-client 依赖。

接口清单：
  generate_video(prompt, image_path, ...)      → 同步生成并下载视频，返回本地路径
  generate_video_async(prompt, image_path, ...) → 提交任务，返回 prompt_id
  poll_video(prompt_id)                         → 轮询任务状态
  download_video(filename, output_path)         → 下载视频文件
  health_check()                                → 检查 ComfyUI 服务器状态

配置（.env）：
  COMFYUI_SERVER=192.168.1.100:8188     ← ComfyUI 服务器地址（必填）
  COMFYUI_WORKFLOW_PATH=...             ← 可选，workflow JSON 路径

使用示例：
  from services.comfyui_video import generate_video
  result = generate_video(
      prompt="A gentle breeze blowing through cherry blossoms",
      image_path="outputs/images/scene_01.png",
  )
  # result = {"ok": True, "path": "outputs/comfyui/xxx.mp4", "prompt_id": "..."}
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ── 配置 ────────────────────────────────────────────────────────────────────
_COMFYUI_SERVER = os.getenv("COMFYUI_SERVER", "127.0.0.1:8188")
_WORKFLOW_PATH = os.getenv("COMFYUI_WORKFLOW_PATH", "")

# 输出目录
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "comfyui"

# 默认 API prompt 模板（基于 LTX-2.3 i2v workflow 节点映射）
# 如果有 workflow JSON 文件，优先从文件加载
_DEFAULT_API_PROMPT: Dict[str, Any] = {
    "3": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["2", 0]}
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "blurry, low quality, distorted, watermark", "clip": ["2", 0]}
    },
    "11": {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": 10}
    },
    "27": {
        "class_type": "INTConstant",
        "inputs": {"value": 105}
    },
    "43": {
        "class_type": "EmptyLTXVLatentVideo",
        "inputs": {"width": 768, "height": 512}
    },
    "9": {
        "class_type": "LTXVScheduler",
        "inputs": {"steps": 20}
    },
    "18": {
        "class_type": "GuiderParameters",
        "inputs": {"cfg": 3}
    },
    "19": {
        "class_type": "GuiderParameters",
        "inputs": {"cfg": 7}
    },
    "23": {
        "class_type": "FloatConstant",
        "inputs": {"value": 25}
    },
    "45": {
        "class_type": "LTXVAddGuide",
        "inputs": {"strength": 1}
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════════════════════════════

def _base_url() -> str:
    server = _COMFYUI_SERVER.rstrip("/")
    if not server.startswith("http"):
        server = f"http://{server}"
    return server


def _load_api_prompt(workflow_path: Optional[str] = None) -> Dict[str, Any]:
    """加载 API prompt 模板。优先从 workflow JSON 的 extra.prompt 提取。"""
    path = workflow_path or _WORKFLOW_PATH
    if path and Path(path).exists():
        with open(path, "r", encoding="utf-8") as f:
            wf = json.load(f)
        prompt = wf.get("extra", {}).get("prompt")
        if prompt:
            return prompt
    return {k: {**v, "inputs": {**v["inputs"]}} for k, v in _DEFAULT_API_PROMPT.items()}


def _http_get(path: str, timeout: int = 30) -> Dict[str, Any]:
    r = requests.get(f"{_base_url()}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def _http_post(path: str, data: Any, timeout: int = 60) -> Any:
    r = requests.post(f"{_base_url()}{path}", json=data, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _upload_image(image_path: str) -> str:
    """上传图片到 ComfyUI input 目录，返回服务器端文件名。"""
    image_path = str(Path(image_path).resolve())
    filename = os.path.basename(image_path)
    with open(image_path, "rb") as f:
        r = requests.post(
            f"{_base_url()}/upload/image",
            files={"image": (filename, f, "application/octet-stream")},
            data={"overwrite": "true"},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()["name"]


# ═══════════════════════════════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════════════════════════════

def health_check(server: Optional[str] = None) -> Dict[str, Any]:
    """
    检查 ComfyUI 服务器状态。

    Returns:
        {"ok": True, "system": {...}, "queue": {"running": N, "pending": N}}
        或 {"ok": False, "error": "原因"}
    """
    old = _COMFYUI_SERVER
    try:
        if server:
            global _COMFYUI_SERVER
            _COMFYUI_SERVER = server
        stats = _http_get("/system_stats")
        queue = _http_get("/queue")
        return {
            "ok": True,
            "system": stats,
            "queue": {
                "running": len(queue.get("queue_running", [])),
                "pending": len(queue.get("queue_pending", [])),
            },
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _COMFYUI_SERVER = old


def generate_video_async(
    prompt: str,
    image_path: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    seed: Optional[int] = None,
    frames: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    workflow_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    提交 LTX-2.3 i2v 视频生成任务（异步）。

    Args:
        prompt:          正向提示词
        image_path:      输入图片路径（首帧），可选
        negative_prompt: 负向提示词
        seed:            随机种子
        frames:          总帧数，默认 105
        width:           视频宽度，默认 768
        height:          视频高度，默认 512
        workflow_path:   workflow JSON 路径，默认从 .env 或内置模板

    Returns:
        {"ok": True, "prompt_id": "xxx"} 或 {"ok": False, "error": "原因"}
    """
    try:
        api_prompt = _load_api_prompt(workflow_path)

        # 设置提示词
        if "3" in api_prompt:
            api_prompt["3"]["inputs"]["text"] = prompt
        if negative_prompt and "4" in api_prompt:
            api_prompt["4"]["inputs"]["text"] = negative_prompt

        # 设置参数
        if seed is not None and "11" in api_prompt:
            api_prompt["11"]["inputs"]["noise_seed"] = seed
        if frames is not None and "27" in api_prompt:
            api_prompt["27"]["inputs"]["value"] = frames
        if width is not None and "43" in api_prompt:
            api_prompt["43"]["inputs"]["width"] = width
        if height is not None and "43" in api_prompt:
            api_prompt["43"]["inputs"]["height"] = height

        # 上传图片
        if image_path:
            uploaded_name = _upload_image(image_path)
            # 更新 LoadImage 节点
            for nid, node in api_prompt.items():
                if node.get("class_type") == "LoadImage":
                    node["inputs"]["image"] = uploaded_name
                    break

        # 提交
        client_id = str(uuid.uuid4())
        resp = _http_post("/prompt", {"prompt": api_prompt, "client_id": client_id})
        prompt_id = resp.get("prompt_id", "")
        if not prompt_id:
            return {"ok": False, "error": f"未获得 prompt_id: {resp}"}

        return {"ok": True, "prompt_id": prompt_id}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def poll_video(
    prompt_id: str,
    max_wait: int = 600,
    interval: int = 5,
) -> Dict[str, Any]:
    """
    轮询 ComfyUI 视频生成任务状态。

    Args:
        prompt_id: generate_video_async 返回的 prompt_id
        max_wait:  最大等待秒数，默认 600（视频生成较慢）
        interval:  轮询间隔秒数，默认 5

    Returns:
        成功: {"ok": True, "files": [{"filename": "...", "subfolder": "..."}]}
        失败: {"ok": False, "error": "原因"}
    """
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        try:
            history = _http_get(f"/history/{prompt_id}")
            if prompt_id not in history:
                continue  # 还在执行中

            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = status.get("messages", [])
                return {"ok": False, "error": f"执行错误: {msgs}"}

            # 收集输出文件
            outputs = entry.get("outputs", {})
            files: List[Dict[str, str]] = []
            for node_id, output in outputs.items():
                for gif in output.get("gifs", []):
                    files.append({
                        "filename": gif["filename"],
                        "subfolder": gif.get("subfolder", ""),
                        "type": gif.get("type", "output"),
                    })
                for img in output.get("images", []):
                    files.append({
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    })

            if not files:
                return {"ok": False, "error": "执行完成但无输出文件"}

            return {"ok": True, "files": files}

        except requests.exceptions.ConnectionError:
            return {"ok": False, "error": "ComfyUI 服务器连接失败"}
        except Exception:
            pass  # 继续轮询

    return {"ok": False, "error": f"超时 ({max_wait}s)"}


def download_video(
    filename: str,
    output_path: Optional[str] = None,
    subfolder: str = "",
    output_type: str = "output",
) -> Dict[str, Any]:
    """
    从 ComfyUI 下载输出文件。

    Args:
        filename:    文件名
        output_path: 保存路径，默认 outputs/comfyui/<filename>
        subfolder:   子目录
        output_type: 输出类型，默认 "output"

    Returns:
        {"ok": True, "path": "...", "size_bytes": N} 或 {"ok": False, "error": "原因"}
    """
    if not output_path:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(_OUTPUT_DIR / filename)

    try:
        params = {"filename": filename, "subfolder": subfolder, "type": output_type}
        r = requests.get(f"{_base_url()}/view", params=params, timeout=120)
        r.raise_for_status()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(r.content)
        return {"ok": True, "path": output_path, "size_bytes": len(r.content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def generate_video(
    prompt: str,
    image_path: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    seed: Optional[int] = None,
    frames: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    output_path: Optional[str] = None,
    workflow_path: Optional[str] = None,
    max_wait: int = 600,
) -> Dict[str, Any]:
    """
    一站式视频生成：上传图片 → 提交 → 轮询 → 下载（同步阻塞）。

    Args:
        prompt:          正向提示词
        image_path:      输入图片路径（首帧）
        negative_prompt: 负向提示词
        seed:            随机种子
        frames:          帧数，默认 105
        width / height:  分辨率，默认 768x512
        output_path:     保存路径，默认自动生成
        workflow_path:   workflow JSON 路径
        max_wait:        最大等待秒数

    Returns:
        成功: {"ok": True, "path": "outputs/comfyui/xxx.mp4", "prompt_id": "...", "files": [...]}
        失败: {"ok": False, "error": "原因"}

    Example:
        result = generate_video(
            prompt="A gentle breeze blowing through cherry blossoms, cinematic",
            image_path="outputs/images/scene_01.png",
            seed=42,
            frames=105,
        )
    """
    # 提交
    submit = generate_video_async(
        prompt=prompt,
        image_path=image_path,
        negative_prompt=negative_prompt,
        seed=seed,
        frames=frames,
        width=width,
        height=height,
        workflow_path=workflow_path,
    )
    if not submit["ok"]:
        return submit
    prompt_id = submit["prompt_id"]

    # 轮询
    result = poll_video(prompt_id, max_wait=max_wait)
    if not result["ok"]:
        return result

    # 下载第一个视频文件
    files = result["files"]
    video_file = next((f for f in files if f["filename"].endswith(".mp4")), files[0])
    dl = download_video(
        video_file["filename"],
        output_path=output_path,
        subfolder=video_file.get("subfolder", ""),
        output_type=video_file.get("type", "output"),
    )
    if not dl["ok"]:
        return dl

    return {
        "ok": True,
        "path": dl["path"],
        "prompt_id": prompt_id,
        "files": files,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 业务层快捷函数
# ═══════════════════════════════════════════════════════════════════════════════

def generate_scene_video(
    scene_description: str,
    image_path: str,
    output_path: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    追思影像专用：根据分镜描述和首帧图片生成视频。

    Args:
        scene_description: 分镜描述（来自 storyboard）
        image_path:        首帧图片路径
        output_path:       保存路径
        seed:              随机种子

    Returns:
        同 generate_video

    Example:
        result = generate_scene_video(
            scene_description="老人在公园里打太极拳，晨光透过树叶洒落，温暖怀旧的氛围",
            image_path="outputs/images/scene_03.png",
        )
    """
    return generate_video(
        prompt=scene_description,
        image_path=image_path,
        output_path=output_path,
        seed=seed,
        frames=105,     # ~4s @ 25fps
        width=768,
        height=512,
    )
