# backend/services/suno_bgm.py
"""
Suno BGM 生成模块 — 独立可调用，通过 302.ai 网关调用 Suno API。

接口清单：
  generate_bgm(tags, title, ...)           → 同步生成并下载 BGM，返回文件路径
  generate_bgm_async(tags, title, ...)     → 提交任务，返回 task_id
  poll_bgm(task_id)                        → 轮询任务状态
  download_bgm(audio_url, output_path)     → 下载音频文件
  build_memorial_tags(style, keywords)     → 根据追思风格生成 Suno tags

使用示例：
  from services.suno_bgm import generate_bgm, build_memorial_tags
  tags = build_memorial_tags("warm_nostalgia", ["父亲", "太极", "书法"])
  result = generate_bgm(tags=tags, title="追思 · 陈文斌")
  # result = {"ok": True, "path": "outputs/bgm/xxx.mp3", "duration": 180.5}

配置：复用 .env 中的 AI302_API_KEY（与 llm_client.py 一致）
"""
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ── 配置（复用项目 .env）─────────────────────────────────────────────────────
_302_API_KEY = os.getenv("AI302_API_KEY", "")
_302_BASE_RAW = "https://api.302.ai"  # 非 /v1 端点

# Suno 模型版本
SUNO_MODEL = "chirp-v4"

# 输出目录
_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "bgm"

# 追思风格 → Suno tags 映射
_MEMORIAL_STYLE_MAP: Dict[str, str] = {
    "warm_nostalgia":     "warm, nostalgic, gentle, piano, strings, emotional, bittersweet",
    "solemn_tribute":     "solemn, orchestral, dignified, slow, reverent, cinematic",
    "peaceful_farewell":  "peaceful, serene, acoustic guitar, soft, ambient, healing",
    "celebration_of_life":"uplifting, hopeful, piano, warm, bright, inspirational",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════════════════════════════

def build_memorial_tags(
    style: str = "warm_nostalgia",
    keywords: Optional[List[str]] = None,
) -> str:
    """
    根据追思风格和关键词生成 Suno tags 字符串。

    Args:
        style: 风格标识，可选 warm_nostalgia / solemn_tribute / peaceful_farewell / celebration_of_life
        keywords: 附加关键词，如 ["父亲", "太极", "书法"]

    Returns:
        Suno tags 字符串，如 "warm, nostalgic, gentle, piano, memorial, instrumental"
    """
    base = _MEMORIAL_STYLE_MAP.get(style, _MEMORIAL_STYLE_MAP["warm_nostalgia"])
    extras = ", memorial, cinematic, instrumental"
    if keywords:
        extras = ", " + ", ".join(keywords) + extras
    return base + extras


def generate_bgm_async(
    tags: str,
    title: str = "追思 BGM",
    make_instrumental: bool = True,
    model: str = SUNO_MODEL,
) -> Dict[str, Any]:
    """
    提交 Suno BGM 生成任务（异步）。

    Args:
        tags:       风格描述，如 "warm, nostalgic, piano, memorial, instrumental"
        title:      曲目标题
        make_instrumental: 是否纯音乐（无人声），默认 True
        model:      Suno 模型版本，默认 chirp-v4

    Returns:
        {"ok": True, "task_id": "xxx"} 或 {"ok": False, "error": "原因"}
    """
    if not _302_API_KEY:
        return {"ok": False, "error": "AI302_API_KEY 未配置"}

    headers = {
        "Authorization": f"Bearer {_302_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "tags": tags,
        "mv": model,
        "title": title,
        "make_instrumental": make_instrumental,
    }
    try:
        r = requests.post(
            f"{_302_BASE_RAW}/suno/submit/music",
            headers=headers, json=body, timeout=60,
        )
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        resp = r.json()
        task_id = resp.get("data", "")
        if not task_id:
            return {"ok": False, "error": f"未获得 task_id: {resp}"}
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def poll_bgm(
    task_id: str,
    max_wait: int = 600,
    interval: int = 15,
) -> Dict[str, Any]:
    """
    轮询 Suno BGM 任务状态，阻塞直到完成或超时。

    Args:
        task_id:   generate_bgm_async 返回的 task_id
        max_wait:  最大等待秒数，默认 300
        interval:  轮询间隔秒数，默认 15

    Returns:
        成功: {"ok": True, "audio_url": "https://...", "duration": 180.5, "title": "..."}
        失败: {"ok": False, "error": "原因"}
    """
    if not _302_API_KEY:
        return {"ok": False, "error": "AI302_API_KEY 未配置"}

    headers = {"Authorization": f"Bearer {_302_API_KEY}"}
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        try:
            r = requests.get(
                f"{_302_BASE_RAW}/suno/fetch/{task_id}",
                headers=headers, timeout=20,
            )
            pd = r.json()
            data = pd.get("data", {})
            status = data.get("status", "") if isinstance(data, dict) else ""

            # 完成
            if status in ("complete", "SUCCESS"):
                items = data.get("data", []) if isinstance(data, dict) else []
                if isinstance(items, list) and items:
                    item = items[0]
                    return {
                        "ok": True,
                        "audio_url": item.get("audio_url", ""),
                        "duration": item.get("metadata", {}).get("duration"),
                        "title": item.get("title", ""),
                    }
                return {"ok": False, "error": "任务完成但无音频数据"}

            # 失败
            if status in ("failed", "FAILED"):
                reason = data.get("fail_reason", "未知") if isinstance(data, dict) else "未知"
                return {"ok": False, "error": f"BGM 生成失败: {reason}"}

            # streaming 阶段也可能已有可用音频
            items = data.get("data", []) if isinstance(data, dict) else []
            if isinstance(items, list):
                for item in items:
                    url = item.get("audio_url", "")
                    dur = item.get("metadata", {}).get("duration")
                    if url and dur and dur > 0:
                        return {
                            "ok": True,
                            "audio_url": url,
                            "duration": dur,
                            "title": item.get("title", ""),
                        }
        except Exception:
            pass  # 网络抖动，继续轮询

    return {"ok": False, "error": f"超时 ({max_wait}s)"}


def download_bgm(
    audio_url: str,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    下载 BGM 音频文件到本地。

    Args:
        audio_url:   Suno 返回的音频 URL
        output_path: 保存路径，默认 outputs/bgm/<timestamp>.mp3

    Returns:
        {"ok": True, "path": "outputs/bgm/xxx.mp3"} 或 {"ok": False, "error": "原因"}
    """
    if not output_path:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(_OUTPUT_DIR / f"bgm_{int(time.time())}.mp3")

    try:
        r = requests.get(audio_url, timeout=120)
        r.raise_for_status()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(r.content)
        return {"ok": True, "path": output_path, "size_bytes": len(r.content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def generate_bgm(
    tags: str,
    title: str = "追思 BGM",
    make_instrumental: bool = True,
    output_path: Optional[str] = None,
    max_wait: int = 300,
) -> Dict[str, Any]:
    """
    一站式 BGM 生成：提交 → 轮询 → 下载（同步阻塞）。

    Args:
        tags:       Suno 风格描述
        title:      曲目标题
        make_instrumental: 是否纯音乐，默认 True
        output_path: 保存路径，默认自动生成
        max_wait:   最大等待秒数

    Returns:
        成功: {"ok": True, "path": "outputs/bgm/xxx.mp3", "duration": 180.5, "task_id": "xxx"}
        失败: {"ok": False, "error": "原因"}
    """
    # 提交
    submit = generate_bgm_async(tags, title, make_instrumental)
    if not submit["ok"]:
        return submit
    task_id = submit["task_id"]

    # 轮询
    result = poll_bgm(task_id, max_wait=max_wait)
    if not result["ok"]:
        return result

    # 下载
    dl = download_bgm(result["audio_url"], output_path)
    if not dl["ok"]:
        return dl

    return {
        "ok": True,
        "path": dl["path"],
        "duration": result.get("duration"),
        "task_id": task_id,
        "title": result.get("title", title),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 追思专用快捷函数（面向业务）
# ═══════════════════════════════════════════════════════════════════════════════

def generate_memorial_bgm(
    deceased_name: str,
    style: str = "warm_nostalgia",
    keywords: Optional[List[str]] = None,
    output_path: Optional[str] = None,
    max_wait: int = 300,
) -> Dict[str, Any]:
    """
    追思影像专用 BGM 生成（业务层封装）。

    Args:
        deceased_name: 逝者姓名，用于曲目标题
        style:         风格偏好（来自表单 style_preference）
        keywords:      附加关键词（来自人物经历，如 ["工程师", "书法", "太极拳"]）
        output_path:   保存路径
        max_wait:      最大等待秒数

    Returns:
        同 generate_bgm

    Example:
        result = generate_memorial_bgm("陈文斌", "warm_nostalgia", ["工程师", "书法", "太极拳"])
    """
    tags = build_memorial_tags(style, keywords)
    title = f"追思 · {deceased_name}"
    return generate_bgm(tags, title, make_instrumental=True,
                        output_path=output_path, max_wait=max_wait)
