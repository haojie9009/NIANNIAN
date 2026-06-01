# backend/routers/audio.py — TTS 合成 + BGM 匹配 API
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import service_manager as sm, session_store
from logger import svc_logger

router = APIRouter(prefix="/audio", tags=["audio"])


# ─── TTS 合成 ─────────────────────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str
    emotion: str = "neutral"
    speed: float = 1.0


@router.post("/tts/{sid}")
def tts_synthesize(sid: str, req: TTSRequest):
    """将文本合成为 MP3 音频，调用 service_manager.synthesize_tts_text。"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "文本不能为空")

    # 用 LLM 清理 SSML/HTML 标签
    clean_text = _clean_ssml_for_tts(text)
    if not clean_text:
        raise HTTPException(400, "清理后文本为空")

    result = sm.synthesize_tts_text(clean_text, sid, scene_idx=0)
    if not result:
        raise HTTPException(500, "TTS 合成失败，未返回音频数据")

    url, duration = result
    svc_logger.info("[api.tts] sid=%s text_len=%d duration=%.1fs", sid, len(clean_text), duration)
    return {"audio_url": url, "duration_sec": round(duration, 1)}


def _clean_ssml_for_tts(raw_text: str) -> str:
    """用 LLM 清理 SSML/HTML 标签，保留停顿和语气意图。"""
    import re

    # 先用正则做一轮简单清理
    cleaned = re.sub(r'<break\s+[^>]*/?>', '，', raw_text)
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    cleaned = cleaned.replace('&nbsp;', ' ').replace('&amp;', '和').strip()

    if len(cleaned) >= len(raw_text) * 0.7:
        return cleaned

    # LLM 语义化整理
    prompt = (
        f"请将以下可能包含标记标签的文本整理为纯净的自然语言文本。\n"
        f"保留停顿、语气转折的语义（用逗号、句号、省略号表达），去除所有 HTML/SSML/XML 标签。\n"
        f"只返回纯文本，不要任何解释。\n\n输入：{raw_text}"
    )
    try:
        result = sm.call_freeform(
            "你是文本清理助手，只返回纯文本结果。",
            prompt,
        )
        result = (result or "").strip()
        if result and len(result) > 5:
            return result
    except Exception:
        pass

    return cleaned


# ─── BGM 匹配 ─────────────────────────────────────────────────────────
@router.post("/bgm/{sid}")
def match_bgm_endpoint(sid: str, force: bool = False):
    """根据 session 上下文，用 LLM 分析情感基调并匹配 BGM。"""
    try:
        session_store.require(sid)
    except KeyError:
        raise HTTPException(404, "session not found")

    return sm.match_bgm(sid, force=force)
