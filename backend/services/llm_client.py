"""
念念 LLM 客户端 — 接入 302.ai 统一网关 + 可灵官方 API
任务与模型映射：
  文本结构化分析（家属访谈 / MV01-06）: claude-sonnet-4-6  →（失败自动回退）→ gpt-5.4
  图像内容理解（describe_image）       : gemini-2.0-pro-image-preview
  图像生成（generate_image_302）        : gemini-2.0-pro-image-preview
  视频生成（generate_video_kling）      : 优先可灵官方直连（kling-v3，首帧模式）
                                         → 自动回退 302.ai（m2v_26_image2video_5s）
  语音转写（transcribe_audio）          : whisper-1
配置项（填写 .env 文件）：
  AI302_API_KEY          = sk-xxxxxxxxxxxx       ← 必填，图文/视频 302.ai 备用均使用
  AI302_TEXT_MODEL       = claude-sonnet-4-6
  AI302_TEXT_FALLBACK    = gpt-5.4
  AI302_VISION_MODEL     = gemini-2.0-pro-image-preview
  AI302_IMAGE_GEN_MODEL  = gemini-2.0-pro-image-preview
  AI302_AUDIO_MODEL      = whisper-1
  KLING_ACCESS_KEY_ID    = （可灵官方 AccessKey ID，留空则自动走 302.ai 备用）
  KLING_ACCESS_KEY_SECRET= （可灵官方 AccessKey Secret，留空则自动走 302.ai 备用）
  LOCAL_LLM_BASE_URL     =  （本地备用，可留空）
  LOCAL_LLM_MODEL        =
"""
import base64
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path
from logger import svc_logger

import requests as _requests
from dotenv import load_dotenv
from openai import OpenAI

from core.storage import DATA_DIR

from logger import cache_logger as _cache_log, playback_logger as _pb_log
import logging as _logging
_llm_log = _logging.getLogger("niannian.llm")

# Ctrl+C 中断 BGM 轮询用 — FastAPI shutdown 时由 main.py 设置
bgm_shutdown = threading.Event()

# Auto-find project root .env regardless of cwd
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_project_root, ".env"), override=True)

# ── 302.ai 网关 ───────────────────────────────────────────────────────────────
_302_BASE_URL = "https://api.302.ai/v1"
_302_VIDEO_I2V_URL = "https://api.302.ai/klingai/m2v_26_image2video_5s"
_302_VIDEO_FETCH_URL = "https://api.302.ai/klingai/task"
_KLING_OFFICIAL_BASE = "https://api-singapore.klingai.com"

_302_VIDEO_SEED_I2V_URL = "https://api.302.ai/volcengine/api/v3/contents/generations/tasks"
_302_VIDEO_SEED_FETCH_URL = "https://api.302.ai/volcengine/api/v3/contents/generations/tasks"



_llm_log.info("[llm] LLM 模式: REAL(302.ai), text_base=%s, video_base=%s",
              _302_BASE_URL, _KLING_OFFICIAL_BASE)
_302_API_KEY  = os.getenv("AI302_API_KEY", "sk-填写您的302.ai密钥")

# ── 各任务专属模型 ─────────────────────────────────────────────────────────────
# 文本分析：主力 Claude，自动回退到 GPT-5.4
TEXT_MODEL          = os.getenv("AI302_TEXT_MODEL",      "claude-sonnet-4-6")
TEXT_FALLBACK_MODEL = os.getenv("AI302_TEXT_FALLBACK",   "gpt-5.4")

# 分镜制作（MV04）专属：gpt-4o（速度快、结构化能力强）
STORYBOARD_MODEL    = os.getenv("AI302_STORYBOARD_MODEL", "gpt-4o")

# 数字人对话 & 人设融合（速度优先）
DIALOGUE_MODEL      = os.getenv("AI302_DIALOGUE_MODEL",  "doubao-Seed-2-0-lite")

# 图像 / 视频 / 音频（固定模型，不回退）
VISION_MODEL        = os.getenv("AI302_VISION_MODEL",       "gemini-2.5-flash")
IMAGE_GEN_MODEL     = os.getenv("AI302_IMAGE_GEN_MODEL",    "google/nano-banana/text-to-image")
IMAGE_GEN_FALLBACK  = os.getenv("AI302_IMAGE_GEN_FALLBACK", "gpt-4o-image-generation")
IMAGE_REF_MODEL     = os.getenv("AI302_IMAGE_REF_MODEL",    "gemini-3-pro-image-preview")
VIDEO_GEN_MODEL     = os.getenv("AI302_VIDEO_GEN_MODEL",    "kling-v1-5-pro")
AUDIO_MODEL         = os.getenv("AI302_AUDIO_MODEL",        "whisper-1")
SEED_VIDEO_GEN_MODEL=os.getenv("AI_302_SEED_VIDEO_GEN_MODEL", "doubao-seedance-2-0-fast-260128")

# ── 图床（首帧图上传，用于 Kling 图生视频）──────────────────────────────────────
IMGBB_API_KEY       = os.getenv("IMGBB_API_KEY", "")

# ── 本地 LLM 备用（可选） ──────────────────────────────────────────────────────
_LOCAL_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "")
_LOCAL_API_KEY  = os.getenv("LOCAL_LLM_API_KEY",  "lm-studio")
_LOCAL_MODEL    = os.getenv("LOCAL_LLM_MODEL",    "")


# ── 录制/回放缓存 ─────────────────────────────────────────────────────────────
# 先调真实 API 跑一遍，保存响应到 backend/data/cache/
# 后续测试用 playback 模式从缓存读取，零 API 消费
_CACHE_MODE = os.getenv("_CACHE_MODE", "").lower().strip()  # record | playback | ""
_cache_log = _logging.getLogger("niannian.cache")

if _CACHE_MODE == "record":
    _cache_log.info("[cache] 模式: RECORD — 真实 API 调用 + 保存响应到 data/cache/")
elif _CACHE_MODE == "playback":
    _cache_log.info("[cache] 模式: PLAYBACK — 从缓存读取，不调用 API")
else:
    _cache_log.info("[cache] 模式: OFF（直连 API）")


class _ResponseCache:
    """请求响应缓存，基于 (endpoint, model, params_hash) 做 key。"""

    _CACHE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "cache"
    )

    def __init__(self):
        if not os.path.isdir(self._CACHE_DIR):
            os.makedirs(self._CACHE_DIR, exist_ok=True)

    def _cache_key(self, endpoint: str, model: str = "", params: dict = None) -> str:
        """生成缓存文件名：endpoint_model_hash(params).json"""
        param_str = json.dumps(params or {}, sort_keys=True, default=str)
        param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]
        model_tag = model.replace("/", "_").replace("-", "_") if model else "nomodel"
        ep_tag = endpoint.strip("/").replace("/", "_")
        return f"{ep_tag}_{model_tag}_{param_hash}"

    def save(self, endpoint: str, response_data: dict, model: str = "", params: dict = None):
        key = self._cache_key(endpoint, model, params)
        path = os.path.join(self._CACHE_DIR, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"endpoint": endpoint, "model": model, "response": response_data,
                        "params": params or {}, "saved_at": datetime.now().isoformat()},
                       f, ensure_ascii=False, indent=2)
        _cache_log.info("[cache] RECORD 保存: %s (%d chars) → %s", key, len(json.dumps(response_data)), os.path.basename(path))

    def load(self, endpoint: str, model: str = "", params: dict = None) -> Optional[dict]:
        import inspect as _inspect
        key = self._cache_key(endpoint, model, params)
        path = os.path.join(self._CACHE_DIR, f"{key}.json")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 找到第一个不在本文件内的调用方
            caller = "unknown"
            _this_file = os.path.abspath(__file__)
            for frame_info in _inspect.stack()[1:]:
                if os.path.abspath(frame_info.filename) != _this_file:
                    caller = f"{os.path.basename(frame_info.filename)}:{frame_info.lineno} {frame_info.function}()"
                    break
            _cache_log.info(
                "[cache] HIT | model=%s | 调用方: %s | 文件: %s | 时间: %s",
                model,
                caller,
                os.path.basename(path),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            _pb_log.info(
                "[cache] HIT | model=%s | 调用方: %s | 文件: %s | 时间: %s",
                model,
                caller,
                os.path.basename(path),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            return data["response"]
        return None

    def list_cache(self) -> list:
        """列出所有缓存项"""
        if not os.path.isdir(self._CACHE_DIR):
            return []
        items = []
        for fn in sorted(os.listdir(self._CACHE_DIR)):
            if fn.endswith(".json"):
                path = os.path.join(self._CACHE_DIR, fn)
                with open(path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                items.append({
                    "file": fn,
                    "endpoint": meta.get("endpoint", ""),
                    "model": meta.get("model", ""),
                    "saved_at": meta.get("saved_at", ""),
                    "params": meta.get("params", {}),
                })
        return items


_cache = _ResponseCache()


def _build_client(base_url: Optional[str], api_key: Optional[str]) -> OpenAI:
    if base_url:
        return OpenAI(api_key=api_key or "none", base_url=base_url)
    return OpenAI(api_key=api_key or "none")


# 主客户端 — 302.ai（文本 + 图像 + 视频 + 音频 全走这里）
PRIMARY_CLIENT: OpenAI = _build_client(_302_BASE_URL, _302_API_KEY)

# 本地备用客户端（仅当环境变量配置时启用）
_LOCAL_CLIENT: Optional[OpenAI] = (
    _build_client(_LOCAL_BASE_URL, _LOCAL_API_KEY)
    if _LOCAL_BASE_URL and _LOCAL_MODEL
    else None
)

# 兼容旧调用
PRIMARY_MODEL = TEXT_MODEL
FALLBACK_MODELS: list = []


def _text_model_queue() -> List[Tuple[str, OpenAI]]:
    """
    文本推理优先级队列：
      1. claude-sonnet-4-6  （主力）
      2. gpt-5.4            （自动回退）
      3. 本地 LLM           （可选）
    """
    q: List[Tuple[str, OpenAI]] = [
        (TEXT_MODEL, PRIMARY_CLIENT),
        (TEXT_FALLBACK_MODEL, PRIMARY_CLIENT),
    ]
    if _LOCAL_CLIENT and _LOCAL_MODEL:
        q.append((_LOCAL_MODEL, _LOCAL_CLIENT))
    return q


def _storyboard_model_queue() -> List[Tuple[str, OpenAI]]:
    """
    分镜制作（MV04）专属优先级队列：
      1. gpt-4o  （速度快、结构化稳定）
      2. gpt-5.4 （备用）
    """
    return [
        (STORYBOARD_MODEL,    PRIMARY_CLIENT),
        (TEXT_FALLBACK_MODEL, PRIMARY_CLIENT),
    ]


# ── 旧接口兼容层 ───────────────────────────────────────────────────────────────
def _iter_model_clients():
    return _text_model_queue()


# ── 模型兼容工具 ──────────────────────────────────────────────────────────────
_JSON_MODE_UNSUPPORTED = ("claude", "gemini")


def _supports_json_mode(model_name: str) -> bool:
    """Claude / Gemini 不支持 response_format=json_object，需手动提取 JSON。"""
    lower = model_name.lower()
    return not any(lower.startswith(prefix) for prefix in _JSON_MODE_UNSUPPORTED)


def _extract_json(text: str) -> str:
    """
    从模型回复中提取 JSON 块。
    优先尝试 ```json ... ``` 代码块，其次查找首个 { 到末尾的内容。
    """
    import re
    # 匹配 ```json ... ``` 或 ``` ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    if m:
        return m.group(1)
    # 取第一个 { 到最后一个 }
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        return text[start:end + 1]
    return text.strip()


def _json_system_hint(system_prompt: str) -> str:
    """对不支持 JSON mode 的模型，在 system prompt 末尾追加 JSON 输出要求。"""
    return (
        system_prompt.rstrip()
        + "\n\n【重要】你的回复必须是且仅是一个合法的 JSON 对象，"
        "不要包含任何 Markdown 标记、解释文字或代码块以外的内容。"
        "直接输出 JSON，不加任何前缀。"
    )


def call_skill(
    skill_name: str,
    system_prompt: str,
    user_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """调用 LLM（JSON 模式），用于 MV 技能 pipeline。
    MV04 分镜制作专用 gpt-4o，其余使用 claude-sonnet-4-6。"""
    # ── Playback: 从缓存读取 ──
    if _CACHE_MODE == "playback":
        cached = _cache.load("chat/completions", TEXT_MODEL,
                             {"skill": skill_name, "payload": user_payload})
        if cached is not None:
            return cached
        _cache_log.info("[cache] MISS | model=%s | func=call_skill | skill=%s → 调用 API", TEXT_MODEL, skill_name)

    # MV04 分镜制作强制使用 storyboard 专属队列（gpt-4o）
    model_queue = _storyboard_model_queue() if skill_name == "MV04" else _iter_model_clients()
    last_error: Optional[str] = None
    _had_fallback = False
    for model_name, client in model_queue:
        use_json_mode = _supports_json_mode(model_name)
        sys_content   = system_prompt if use_json_mode else _json_system_hint(system_prompt)
        for attempt in range(1, 4):
            try:
                _cache_log.info("[cache] CALL  | model=%s | func=call_skill | skill=%s", model_name, skill_name)
                kwargs: Dict[str, Any] = dict(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": sys_content},
                        {"role": "user",   "content": json.dumps(user_payload, ensure_ascii=False)},
                    ],
                    temperature=0.4,
                )
                if use_json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)
                raw = response.choices[0].message.content or "{}"
                if not use_json_mode:
                    raw = _extract_json(raw)
                result = json.loads(raw)
                if _had_fallback:
                    _llm_log.info("[llm] %s 降级到 %s，skill=%s 调用成功", TEXT_MODEL, model_name, skill_name)
                # ── Record: 保存响应（record 模式，或 playback miss 后补存）──
                if _CACHE_MODE in ("record", "playback"):
                    _cache.save("chat/completions", result, model_name,
                                {"skill": skill_name, "payload": user_payload})
                return result
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                if attempt >= 3:
                    _llm_log.warning("[llm] %s 连续3次重试均失败，skill=%s: %s", model_name, skill_name, exc)
                    _had_fallback = True
                if attempt < 3:
                    time.sleep(2)
    return {"error": True, "skill": skill_name, "message": last_error or "Unknown error"}


def call_memorial_chat(
    system_prompt: str,
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
) -> str:
    """
    念念 AI 对话接口：支持多轮对话历史。
    messages: [{"role": "user"|"assistant", "content": "..."}]
    model: 指定模型时直接使用，不走默认队列（如 "doubao-Seed-2-0-lite"）
    返回纯文本回复。
    """
    last_error: Optional[str] = None
    all_msgs = [{"role": "system", "content": system_prompt}] + messages

    # 指定模型时直接调用，不走队列
    if model:
        # ── Playback: 从缓存读取 ──
        if _CACHE_MODE == "playback":
            cached = _cache.load("chat/completions", model, {"messages": messages})
            if cached is not None:
                return cached.get("reply", "")
            _cache_log.info("[cache] MISS | model=%s | func=call_memorial_chat | 指定模型 → 调用 API", model)

        for attempt in range(1, 4):
            try:
                _cache_log.info("[cache] CALL  | model=%s | func=call_memorial_chat", model)
                response = PRIMARY_CLIENT.chat.completions.create(
                    model=model,
                    messages=all_msgs,
                    temperature=0.65,
                    max_tokens=600,
                )
                result = response.choices[0].message.content or ""
                # ── Record: 保存响应 ──
                if _CACHE_MODE in ("record", "playback"):
                    _cache.save("chat/completions", {"reply": result}, model, {"messages": messages})
                return result
            except Exception as exc:
                last_error = f"{model}: {exc}"
                if attempt >= 3:
                    _llm_log.warning("[llm] %s 连续3次重试均失败: %s", model, exc)
                if attempt < 3:
                    time.sleep(1)
        return f"（念念暂时无法回应，请稍后再试。错误：{last_error or '未知'}）"

    if _CACHE_MODE == "playback":
        for _model_name, _ in _iter_model_clients():
            cached = _cache.load("chat/completions", _model_name, {"messages": messages})
            if cached is not None:
                return cached.get("reply", "")
        _cache_log.info("[cache] MISS | model=%s | func=call_memorial_chat → 调用 API", TEXT_MODEL)

    _had_fallback = False
    for model_name, client in _iter_model_clients():
        for attempt in range(1, 4):
            try:
                _cache_log.info("[cache] CALL  | model=%s | func=call_memorial_chat", model_name)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=all_msgs,
                    temperature=0.65,
                    max_tokens=600,
                )
                if _had_fallback:
                    _llm_log.info("[llm] %s 降级到 %s，memorial_chat 成功", TEXT_MODEL, model_name)
                # ── Record: 保存响应 ──
                if _CACHE_MODE in ("record", "playback"):
                    _cache.save("chat/completions", {"reply": response.choices[0].message.content or ""},
                                model_name, {"messages": messages})
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                if attempt >= 3:
                    _llm_log.warning("[llm] %s 连续3次重试均失败，memorial_chat: %s", model_name, exc)
                    _had_fallback = True
                if attempt < 3:
                    time.sleep(2)
    return f"（念念暂时无法回应，请稍后再试。错误：{last_error or '未知'}）"


def call_freeform(system_prompt: str, user_content: str) -> str:
    """自由文本生成。使用 claude-sonnet-4-6。"""
    # ── Playback ──
    if _CACHE_MODE == "playback":
        cached = _cache.load("chat/completions", TEXT_MODEL, {"system": system_prompt, "user": user_content})
        if cached is not None:
            return cached.get("reply", "")
        _cache_log.info("[cache] MISS | model=%s | func=call_freeform → 调用 API", TEXT_MODEL)

    last_error: Optional[str] = None
    _had_fallback = False
    for model_name, client in _iter_model_clients():
        for attempt in range(1, 4):
            try:
                _cache_log.info("[cache] CALL  | model=%s | func=call_freeform", model_name)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_content},
                    ],
                    temperature=0.4,
                )
                if _had_fallback:
                    _llm_log.info("[llm] %s 降级到 %s，freeform 成功", TEXT_MODEL, model_name)
                result = response.choices[0].message.content or ""
                if _CACHE_MODE in ("record", "playback"):
                    _cache.save("chat/completions", {"reply": result}, model_name,
                                {"system": system_prompt, "user": user_content})
                return result
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                if attempt >= 3:
                    _llm_log.warning("[llm] %s 连续3次重试均失败，freeform: %s", model_name, exc)
                    _had_fallback = True
                if attempt < 3:
                    time.sleep(2)
    return f"[ERROR] {last_error or 'Unknown error'}"


def call_structured(system_prompt: str, user_content: str) -> Dict[str, Any]:
    """结构化 JSON 生成。使用 claude-sonnet-4-6。"""
    # ── Playback ──
    if _CACHE_MODE == "playback":
        cached = _cache.load("chat/completions", TEXT_MODEL, {"system": system_prompt, "user": user_content})
        if cached is not None:
            return cached
        _cache_log.info("[cache] MISS | model=%s | func=call_structured → 调用 API", TEXT_MODEL)

    last_error: Optional[str] = None
    _had_fallback = False
    for model_name, client in _iter_model_clients():
        use_json_mode = _supports_json_mode(model_name)
        sys_content   = system_prompt if use_json_mode else _json_system_hint(system_prompt)
        for attempt in range(1, 4):
            try:
                _cache_log.info("[cache] CALL  | model=%s | func=call_structured", model_name)
                kwargs: Dict[str, Any] = dict(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": sys_content},
                        {"role": "user",   "content": user_content},
                    ],
                    temperature=0.2,
                )
                if use_json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)
                raw = response.choices[0].message.content or "{}"
                if not use_json_mode:
                    raw = _extract_json(raw)
                result = json.loads(raw)
                if _had_fallback:
                    _llm_log.info("[llm] %s 降级到 %s，structured 成功", TEXT_MODEL, model_name)
                if _CACHE_MODE in ("record", "playback"):
                    _cache.save("chat/completions", result, model_name,
                                {"system": system_prompt, "user": user_content})
                return result
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                if attempt >= 3:
                    _llm_log.warning("[llm] %s 连续3次重试均失败，structured: %s", model_name, exc)
                    _had_fallback = True
                if attempt < 3:
                    time.sleep(2)
    return {"error": True, "message": last_error or "Unknown error"}


def describe_image(image_bytes: bytes, filename: str) -> str:
    """图像内容理解 — 使用 gemini-2.0-pro-image-preview（通过 302.ai）。"""
    last_error: Optional[str] = None
    encoded   = base64.b64encode(image_bytes).decode("utf-8")
    ext       = filename.rsplit(".", 1)[-1].lower()
    image_url = f"data:image/{ext};base64,{encoded}"
    system_prompt = (
        "你是图像理解助手，请输出简洁的中文描述，并尽量提取图片里的文字信息。"
        "如果有清晰文字，请在描述里包含。"
    )
    for attempt in range(1, 3):
        try:
            response = PRIMARY_CLIENT.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请描述这张图片"},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:  # pragma: no cover
            last_error = f"{VISION_MODEL}: {exc}"
            if attempt >= 2:
                _llm_log.warning("[llm] %s 图片理解连续2次重试失败: %s", VISION_MODEL, exc)
            if attempt < 2:
                time.sleep(1)
    return f"[IMAGE_PARSE_ERROR] {last_error or 'Unknown error'}"


def transcribe_audio(audio_bytes: bytes, filename: str) -> str:
    """语音转写 — 使用 whisper-1（通过 302.ai）。"""
    last_error: Optional[str] = None
    for attempt in range(1, 3):
        try:
            response = PRIMARY_CLIENT.audio.transcriptions.create(
                model=AUDIO_MODEL,
                file=(filename, audio_bytes),
            )
            return getattr(response, "text", "") or ""
        except Exception as exc:  # pragma: no cover
            last_error = f"{AUDIO_MODEL}: {exc}"
            if attempt >= 2:
                _llm_log.warning("[llm] %s 语音转写连续2次重试失败: %s", AUDIO_MODEL, exc)
            if attempt < 2:
                time.sleep(1)
    return f"[AUDIO_PARSE_ERROR] {last_error or 'Unknown error'}"


# ─── 语音合成（seed_tts via 302.ai）────────────────────────────────── 
_SEED_TTS_URL = os.getenv("SEED_TTS_URL", "https://openspeech.bytedance.com/api/v3/tts/unidirectional")
_SEED_APP_ID = os.getenv("SEED_TTS_APP_ID", "")
_SEED_ACCESS_KEY = os.getenv("SEED_TTS_ACCESS_KEY", "")
_SEED_RESOURCE_ID = os.getenv("SEED_TTS_RESOURCE_ID", "")
_SEED_SPEAKER = os.getenv("SEED_TTS_SPEAKER", "zh_female_wenroumama_uranus_bigtts")
_SEED_SAMPLE_RATE = 24000


def seed_tts(text: str) -> Optional[bytes]:
    """调用 Seed TTS 合成音频，返回 WAV 字节。

    流式 JSON Lines 响应：每行 base64 音频数据，code=20000000 表示合成结束。
    """
    if not (_SEED_APP_ID and _SEED_ACCESS_KEY and _SEED_RESOURCE_ID):
        _llm_log.warning("[tts] Seed TTS env vars not set")
        return None

    headers = {
        "Authorization": f"Bearer {_302_API_KEY}",
        "X-Api-App-Id": _SEED_APP_ID,
        "X-Api-Access-Key": _SEED_ACCESS_KEY,
        "X-Api-Resource-Id": _SEED_RESOURCE_ID,
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }

    payload = {
        "user": {"uid": "niannian_pipeline"},
        "req_params": {
            "text": text,
            "speaker": _SEED_SPEAKER,
            "audio_params": {
                "format": "wav",
                "sample_rate": _SEED_SAMPLE_RATE,
                "enable_timestamp": True,
                "speech_rate": -20,
            },
            "additions": json.dumps({
                "explicit_language": "zh",
                "disable_markdown_filter": True,
                "enable_timestamp": True,
            }),
        },
    }

    audio_data = bytearray()
    try:
        session = _requests.Session()
        response = session.post(_SEED_TTS_URL, headers=headers, json=payload, stream=True, timeout=120)
        if response.status_code != 200:
            _llm_log.error("[tts] HTTP %d: %s", response.status_code, response.text[:200])
            return None

        for chunk in response.iter_lines(decode_unicode=True):
            if not chunk:
                continue
            data = json.loads(chunk)
            code = data.get("code", 0)
            if code == 0 and "data" in data and data["data"]:
                audio_data.extend(base64.b64decode(data["data"]))
            elif code == 20000000:
                if "usage" in data:
                    _llm_log.info("[tts] usage: %s", data["usage"])
                break
            elif code > 0:
                _llm_log.error("[tts] error %d: %s", code, data.get("message", ""))
                return None

        if not audio_data:
            _llm_log.warning("[tts] no audio data returned")
            return None

        return bytes(audio_data)

    except Exception as e:
        _llm_log.exception("[tts] failed: %s", e)
        return None


# ─── BGM 生成（Suno via 302.ai）─────────────────────────────────────
# 参考 mini-pipeline/run.py step3_5()

_BASE_URL = os.getenv("AI302_BASE_URL", "https://api.302.ai/v1")
_BGM_STYLE_MAP = {
    "warm_nostalgia": "warm, nostalgic, gentle, piano, strings, emotional",
    "peaceful_serene": "peaceful, serene, acoustic guitar, soft, ambient",
    "solemn_respect": "solemn, orchestral, dignified, slow, reverent",
    "gentle_sorrow": "gentle, sorrow, soft piano, emotional, bittersweet",
    "hopeful_gratitude": "uplifting, hopeful, piano, warm, bittersweet",
}


def _set_bgm_progress(sid: str, state: dict) -> None:
    """把 BGM 生成进度写入 session，前端通过 /api/pipeline/status/ 可见。"""
    if not sid:
        return
    try:
        from .service_manager import session_store
        s = session_store.require(sid)
        s.setdefault("bgm_state", {}).update(state)
        # 同时更新 pipeline_state 的 label，让 MV06 进度面板直接显示
        try:
            step = state.get("step", "bgm_polling")
            label = state.get("label", "BGM 生成中…")
            s.setdefault("pipeline_state", {}).setdefault("MV06", {})
            mv06 = s["pipeline_state"]["MV06"]
            mv06["bgm_step"] = step
            mv06["bgm_label"] = label
            mv06["label"] = label
            session_store.update(sid)
        except Exception:
            pass  # pipeline_state 未初始化也不影响
    except Exception:
        pass  # session_store 未初始化时忽略


def generate_bgm_suno(tags: str = "warm, nostalgic, gentle, piano, emotional, memorial, cinematic, instrumental",
                      title: str = "追思", sid: str = "") -> Optional[bytes]:
    """Suno 生成追思 BGM（纯音乐），返回 MP3 字节。

    流程：提交 → 轮询 → 下载。超时 300s。
    """
    api_key = os.getenv("AI302_API_KEY", "")
    if not api_key:
        _llm_log.warning("[bgm] AI302_API_KEY not set")
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    submit_url = f"https://api.302.ai/suno/submit/music"

    body = {
        "tags": tags,
        "mv": "chirp-v4",
        "title": title,
        "make_instrumental": True,
    }

    try:
        r = _requests.post(submit_url, headers=headers, json=body, timeout=60)
        if r.status_code != 200:
            _llm_log.error("[bgm] submit HTTP %d: %s", r.status_code, r.text[:300])
            return None
        resp = r.json()
        task_id = resp.get("data", "")
        if not task_id:
            _llm_log.error("[bgm] 未获得 task_id: %s", r.text[:200])
            return None
        _llm_log.info("[bgm] suno submit OK task_id=%s tags=%s", task_id, tags)
    except Exception as e:
        _llm_log.exception("[bgm] submit failed: %s", e)
        return None
    
    max_wait, elapsed, interval = 600, 0, 15
    fetch_url = f"https://api.302.ai/suno/fetch/{task_id}"
    audio_url = None
    while elapsed < max_wait:
        if bgm_shutdown.wait(interval):
            _llm_log.info("[bgm] 收到 shutdown 信号，停止轮询")
            return None
        elapsed += interval
        try:
            pr = _requests.get(fetch_url, headers={"Authorization": f"Bearer {api_key}"}, 
                               timeout=20)
            pd = pr.json()
            items = pd.get("data", {}).get("data", [])
            if isinstance(items, list):
                for item in items:
                    url = item.get("audio_url", "")
                    dur = float(item.get("duration", "0"))
                    if url and dur and float(dur) > 0:
                        audio_url = url
                        break
                if audio_url:
                    break

            status = pd.get("data", {}).get("status", "")
            if isinstance(pd.get("data"), dict):
                status = pd["data"].get("status", "")
            if status in ("failed", "FAILED"):
                reason = pd.get("data", {}).get("fail_reason", "未知")
                _llm_log.error("[bgm] suno failed: %s", reason)
                return None

        except (_requests.exceptions.ConnectionError, _requests.exceptions.SSLError) as e:
            _llm_log.warning("[bgm] poll transient error: %s — 5s 后重试", e)
            if bgm_shutdown.wait(5):
                _llm_log.info("[bgm] 收到 shutdown 信号，停止轮询")
                return None
            elapsed += 5
            try:
                pr = _requests.get(fetch_url, headers=headers, timeout=20)
                pd = pr.json()
                items = pd.get("data", {}).get("data", [])
                if isinstance(items, list):
                    for item in items:
                        url = item.get("audio_url", "")
                        dur = item.get("metadata", {}).get("duration", 0)
                        if url and dur and float(dur) > 0:
                            audio_url = url
                            break
                    if audio_url:
                        break
                status = pd.get("data", {}).get("status", "")
                if isinstance(pd.get("data"), dict):
                    status = pd["data"].get("status", "")
                if status in ("failed", "FAILED"):
                    reason = pd.get("data", {}).get("fail_reason", "未知")
                    _llm_log.error("[bgm] suno failed: %s", reason)
                    return None
            except Exception as e2:
                _llm_log.warning("[bgm] poll retry exception: %s", e2)
                continue

        except Exception as e:
            _llm_log.warning("[bgm] poll exception: %s", e)

    if not audio_url:
        _llm_log.error("[bgm] suno timeout or no audio_url after %ds", max_wait)
        return None

    # 下载
    try:
        r = _requests.get(audio_url, timeout=120)
        r.raise_for_status()
        _llm_log.info("[bgm] downloaded %d bytes from %s", len(r.content), audio_url)
        return r.content
    except Exception as e:
        _llm_log.exception("[bgm] download failed: %s", e)
        return None


def build_scene_prompts(
    scene: Dict[str, Any],
    character_bible: Optional[Dict[str, Any]] = None,
    scene_library: Optional[List] = None,
    cast_roles: Optional[List[Dict[str, Any]]] = None,
    use_cache: bool = True
) -> Dict[str, Any]:
    """
    根据分镜 + 三要素 + 电影角色表，用 LLM 同时生成：
      - image_prompt : 英文首帧图片 Prompt，锁定角色 DNA 并嵌入角色参考图 URL
      - video_prompt : 中文可灵视频 Prompt
    cast_roles: [{"name":"...", "role_label":"...", "description":"...", "photo_url":"..."}]
      主角（逝者）始终从 ancestor_photo_url 传入，无需在 cast_roles 里重复。
    返回 {"image_prompt": "...", "video_prompt": "...", "_cast_ref": "...（供调用方记录）"}
    """
    # ── 提取角色 DNA 描述 ──────────────────────────────────────────────────────
    dna_lines = []
    if character_bible:
        cd = character_bible.get("character_dna", {})
        if cd.get("facial_features"):
            dna_lines.append(f"面部：{cd['facial_features']}")
        if cd.get("body_features"):
            dna_lines.append(f"体型：{cd['body_features']}")
        if cd.get("clothing_style"):
            dna_lines.append(f"服装：{cd['clothing_style']}")
        if cd.get("mannerisms"):
            dna_lines.append(f"习惯动作：{cd['mannerisms']}")
    dna_text = "；".join(dna_lines) if dna_lines else "（未提供角色DNA）"

    # ── 从 scene_library 匹配场景说明 ─────────────────────────────────────────
    scene_id = scene.get("scene_id", "")
    scene_ref = scene.get("scene_ref") or scene_id
    scene_lib_desc = ""
    if scene_library:
        matched = next(
            (s for s in scene_library if isinstance(s, dict) and s.get("scene_id") == scene_ref),
            None,
        )
        if matched:
            scene_lib_desc = matched.get("visual_descriptor") or matched.get("description", "")

    # ── 构造电影角色表字符串 ──────────────────────────────────────────────────
    cast_lines = []
    if cast_roles:
        for cr in cast_roles:
            _name = cr.get("name", "").strip()
            _rl   = cr.get("role_label", "").strip()
            _desc = cr.get("description", "").strip()
            _url  = cr.get("photo_url", "")
            if not _name:
                continue
            parts = []
            if _rl:
                parts.append(_rl)
            if _desc:
                parts.append(_desc)
            if _url:
                parts.append(f"参考图：{_url}")
            cast_lines.append(f"  · {_name}（{'，'.join(parts) if parts else '配角'}）")
    cast_text = "\n".join(cast_lines) if cast_lines else "（无配角）"

    system_prompt = (
        "你是专业的追思影像 Prompt 工程师，擅长将中文分镜描述转化为高质量 AI 生成 Prompt。\n\n"
        "任务：根据提供的分镜信息、角色DNA和电影角色表，同时输出两条 Prompt：\n\n"
        "1. image_prompt（英文）：\n"
        "   - 必须将主角角色DNA翻译并嵌入，精准锁定外貌\n"
        "   - 若场景中有配角出现，在 prompt 中用格式 [角色名(称谓,参考图URL)] 标注每个配角\n"
        "   - 禁止出现与DNA不符的外貌描述（如：American, Western, blonde等）\n"
        "   - 包含场景光线、构图、情绪基调\n"
        "   - 风格后缀：photorealistic, cinematic still, 8K, warm golden hour lighting\n\n"
        "2. video_prompt（中文）：\n"
        "   - 镜头运动（推镜/拉镜/横移/固定等）\n"
        "   - 人物动作与表情细节（涉及多角色时逐一描述）\n"
        "   - 环境光线变化、情感基调、时长约5-6秒的画面感\n\n"
        "严格按以下 JSON 返回，不要包含任何其他文字：\n"
        '{"image_prompt": "...", "video_prompt": "..."}'
    )

    user_payload = {
        "scene": scene,
        "主角角色DNA（必须锚定）": dna_text,
        "场景环境描述（融入背景）": scene_lib_desc or "（未提供场景描述）",
        "电影配角表（如场景涉及多人，需在prompt中标注）": cast_text,
    }

    result = call_storyboard(system_prompt, json.dumps(user_payload, ensure_ascii=False), use_cache=use_cache)
    if result.get("error") or not result.get("image_prompt"):
        fallback_img = scene.get("mj_prompt") or scene.get("description") or ""
        fallback_vid = scene.get("description") or ""
        return {"image_prompt": fallback_img, "video_prompt": fallback_vid, "_fallback": True}
    result["_cast_ref"] = cast_text
    return result




def _generate_image_wavespeed(prompt: str, model: str) -> tuple:
    """
    调用 302.ai Wavespeed 专属端点生成图像（nano-banana 等）。
    使用同步模式 + base64 输出，无需轮询。
    返回 (b64_string, None) 或 (None, error_msg)。
    """
    import requests as _req
    endpoint = f"https://api.302.ai/ws/api/v3/{model}"
    headers  = {
        "Authorization": f"Bearer {_302_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "prompt":               prompt,
        "aspect_ratio":         "16:9",
        "output_format":        "png",
        "enable_sync_mode":     True,
        "enable_base64_output": True,
    }
    try:
        r = _req.post(endpoint, headers=headers, json=body, timeout=120)
        r.raise_for_status()
        data = r.json().get("data", {})
        outputs = data.get("outputs", [])
        if outputs and isinstance(outputs[0], str) and len(outputs[0]) > 100:
            return outputs[0], None
        # 异步模式：轮询 result
        task_id = data.get("id")
        if task_id:
            poll_url = f"https://api.302.ai/ws/api/v3/predictions/{task_id}/result"
            for _ in range(30):
                import time as _t; _t.sleep(3)
                pr = _req.get(poll_url, headers=headers, timeout=30)
                pd = pr.json().get("data", {})
                if pd.get("status") == "completed":
                    outs = pd.get("outputs", [])
                    if outs:
                        return outs[0], None
                elif pd.get("status") == "failed":
                    return None, pd.get("error", "任务失败")
            return None, "轮询超时（90s）"
        return None, f"API 返回空数据：{r.text[:200]}"
    except Exception as exc:
        return None, str(exc)


def generate_image_302_ref(prompt: str, reference_b64: str) -> tuple:
    """
    有参考照片时的图生图：使用 gemini-3-pro-image-preview（由 AI302_IMAGE_REF_MODEL 控制）。
    流程：
      1. 将参考图 base64 → 上传图床 → 获取公开 HTTPS URL
      2. 将 URL + Prompt 发给 gemini-3-pro-image-preview（302.ai 网关）
      3. 解析响应中的 image 块，返回生成图 base64
    返回 (b64_string, None) 成功；(None, error_message) 失败。
    """
    import logging as _log_ref
    _log_r = _log_ref.getLogger("llm_client.image_ref")

    # ── Playback: 从缓存读取 ──
    if _CACHE_MODE == "playback":
        ref_hash = hashlib.sha256(reference_b64.encode()).hexdigest()[:8]
        cached = _cache.load("chat/completions", IMAGE_REF_MODEL,
                             {"prompt": prompt, "ref_hash": ref_hash})
        if cached is not None and cached.get("b64"):
            return cached["b64"], None
        _cache_log.info("[cache] MISS | model=%s | func=generate_image_302_ref → 调用 API", IMAGE_REF_MODEL)

    # ── Step 1: 上传参考图到图床，获取公开 URL ──────────────────────────────
    try:
        ref_bytes = base64.b64decode(reference_b64)
    except Exception as e:
        return None, f"参考图 base64 解码失败：{e}"

    _log_r.info("[image_ref] 上传参考图到图床...")
    public_url = _upload_image_to_public(ref_bytes, "png")
    if not public_url:
        return None, "参考图上传图床失败，无法获取公开 URL"
    _log_r.info(f"[image_ref] 图床上传成功：{public_url}")

    # ── Step 2: 调用 gemini-3-pro-image-preview ─────────────────────────────
    full_prompt = (
        f"请严格保留参考图中人物的面部特征、年龄、肤色和外貌，将其作为画面主角。"
        f"生成一幅电影感的追思纪念场景：{prompt}。"
        f"风格：电影质感、暖色调、16:9 构图。请直接输出生成的图片。"
    )
    try:
        _cache_log.info("[cache] CALL  | model=%s | func=generate_image_302_ref", IMAGE_REF_MODEL)
        resp = PRIMARY_CLIENT.chat.completions.create(
            model=IMAGE_REF_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": full_prompt},
                        {"type": "image_url", "image_url": {"url": public_url}},
                    ],
                }
            ],
            stream=False,
        )
    except Exception as exc:
        _log_r.warning(f"[image_ref] API 调用失败：{exc}")
        return None, f"gemini-3-pro-image-preview 调用失败：{exc}"

    b64_str, err = _parse_gemini_image_response(resp, "[image_ref]")
    # ── Record: 保存响应 ──
    if _CACHE_MODE in ("record", "playback") and b64_str:
        ref_hash = hashlib.sha256(reference_b64.encode()).hexdigest()[:8]
        _cache.save("chat/completions", {"b64": b64_str, "error": err}, IMAGE_REF_MODEL,
                    {"prompt": prompt, "ref_hash": ref_hash})
    return b64_str, err


def _parse_gemini_image_response(resp, log_tag: str = "") -> tuple:
    """
    解析 gemini-3-pro-image-preview 经由 302.ai 网关返回的图片。
    兼容多种格式：
      A. content 列表中 type=image_url，url 以 data: 开头（base64 data URL）
      B. content 列表中 type=image_url，url 以 http 开头（HTTPS，下载）
      C. content 为字符串，含 markdown 图片 ![...](url)（302.ai 常见格式）
      D. content 为字符串，含裸 HTTPS 图片 URL
      E. 302.ai 特殊格式：content="![image]()" 但图片 base64 藏在 message
         的额外字段（通过 model_dump() 深扫找 data:image 或 base64 字段）
    返回 (b64_string, None) 或 (None, error_str)。
    """
    import re as _re
    import json as _json
    import logging as _log_pg
    _log = _log_pg.getLogger("llm_client.gemini_parse")

    try:
        msg = resp.choices[0].message
        content = msg.content
    except Exception as e:
        return None, f"读取响应 content 失败：{e}"

    # ── 格式 A/B：content 是列表（标准 multipart）────────────────────────────
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                # pydantic 对象尝试转 dict
                try:
                    part = part.model_dump() if hasattr(part, "model_dump") else vars(part)
                except Exception:
                    continue
            ptype = part.get("type", "")
            if ptype == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if url.startswith("data:"):
                    _log.info(f"{log_tag} 格式A：data URL")
                    return url.split(",", 1)[-1], None
                elif url.startswith("http"):
                    b64 = _download_url_to_b64(url, log_tag)
                    if b64:
                        return b64, None
            elif ptype == "image":
                # 部分网关把图片放在 part["image"]{"url"/"data"}
                img_field = part.get("image", {})
                if isinstance(img_field, dict):
                    url = img_field.get("url", "") or img_field.get("data", "")
                    if url.startswith("data:"):
                        return url.split(",", 1)[-1], None
                    elif url.startswith("http"):
                        b64 = _download_url_to_b64(url, log_tag)
                        if b64:
                            return b64, None

    # ── 格式 C/D：content 是字符串，从中提取图片 URL ─────────────────────────
    if isinstance(content, str):
        # 格式C：markdown 图片语法 ![...](url)，要求 URL 非空
        md_matches = _re.findall(r'!\[.*?\]\((https?://[^\s)]+)\)', content)
        for url in md_matches:
            _log.info(f"{log_tag} 格式C：markdown 图片链接 {url[:60]}")
            b64 = _download_url_to_b64(url, log_tag)
            if b64:
                return b64, None

        # 格式D：裸 HTTPS URL（302.ai CDN 域名）
        url_matches = _re.findall(r'https?://\S+\.(?:png|jpg|jpeg|webp|gif)(?:\?\S*)?', content, _re.IGNORECASE)
        # 也匹配 302.ai file CDN
        url_matches += _re.findall(r'https://file\.302\.ai/\S+', content)
        seen = set()
        for url in url_matches:
            url = url.rstrip('.')
            if url in seen:
                continue
            seen.add(url)
            _log.info(f"{log_tag} 格式D：裸 URL {url[:60]}")
            b64 = _download_url_to_b64(url, log_tag)
            if b64:
                return b64, None

    # ── 格式 E：深扫 model_dump() 找藏在其他字段的图片数据 ──────────────────
    # 适用于 ![image]() 空URL 但图片实际在 message 的非标准字段中
    try:
        raw_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
        raw_str = _json.dumps(raw_dict, ensure_ascii=False)

        # E1: 找 data:image/...;base64, 开头的 base64 串
        data_url_hits = _re.findall(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]{200,})', raw_str)
        for hit in data_url_hits:
            _log.info(f"{log_tag} 格式E1：model_dump 中发现 data:image base64（长度={len(hit)}）")
            return hit, None

        # E2: 找 file.302.ai 或其他 CDN URL
        cdn_hits = _re.findall(r'https://file\.302\.ai/[^"\'\\s]+', raw_str)
        cdn_hits += _re.findall(r'https?://[^"\'\\s]+\.(?:png|jpg|jpeg|webp)[^"\'\\s]*', raw_str, _re.IGNORECASE)
        seen_e = set()
        for url in cdn_hits:
            url = url.rstrip('.,\\/"\' ')
            if url in seen_e or '![image]' in url:
                continue
            seen_e.add(url)
            _log.info(f"{log_tag} 格式E2：model_dump 中发现 CDN URL {url[:60]}")
            b64 = _download_url_to_b64(url, log_tag)
            if b64:
                return b64, None

        # E3: 找纯 base64 字段（key 含 "image"/"b64"/"data"）
        def _scan_dict(d, depth=0):
            if depth > 8:
                return None
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, str) and len(v) > 500 and k.lower() in (
                        "b64_json", "image", "data", "base64", "content_b64", "image_data"
                    ):
                        try:
                            import base64 as _b64chk
                            _b64chk.b64decode(v[:64])  # 验证是合法 base64
                            _log.info(f"{log_tag} 格式E3：字段 {k} 中发现 base64（长度={len(v)}）")
                            return v
                        except Exception:
                            pass
                    result = _scan_dict(v, depth + 1)
                    if result:
                        return result
            elif isinstance(d, list):
                for item in d:
                    result = _scan_dict(item, depth + 1)
                    if result:
                        return result
            return None

        b64_hit = _scan_dict(raw_dict)
        if b64_hit:
            return b64_hit, None

    except Exception as _e_scan:
        _log.warning(f"{log_tag} model_dump 深扫出错：{_e_scan}")

    # 真的只有文字
    _log.warning(f"{log_tag} 模型返回纯文字，未找到图片：{str(content)[:120]}")
    return None, f"gemini 返回文字而非图片：{str(content)[:80]}"


def _download_url_to_b64(url: str, log_tag: str = "") -> Optional[str]:
    """下载 HTTPS 图片 URL，返回 base64 字符串；失败返回 None。"""
    import logging as _log_dl
    _log = _log_dl.getLogger("llm_client.download")
    try:
        r = _requests.get(url, timeout=30)
        if r.status_code == 200 and r.content:
            _log.info(f"{log_tag} 下载成功 {len(r.content)//1024}KB")
            return base64.b64encode(r.content).decode()
        _log.warning(f"{log_tag} 下载失败 HTTP {r.status_code}")
    except Exception as e:
        _log.warning(f"{log_tag} 下载异常：{e}")
    return None


def generate_image_302(prompt: str, reference_b64: Optional[str] = None) -> tuple:
    """
    生成图像主入口 —— 统一使用 gemini-3-pro-image-preview（IMAGE_REF_MODEL）。
    · 有参考照片 → 上传图床获取 HTTPS URL，图文一起发给 gemini，保留人物形象
    · 无参考照片 → 纯文本 prompt 发给 gemini，直接生成分镜图
    返回 (b64_string, None) 成功；(None, error_message) 失败。
    """
    import logging as _log_img
    import requests as _rq_img
    _log_i = _log_img.getLogger("llm_client.image")

    # ── 有参考照片：委托 generate_image_302_ref（自带 cache）─────────────────
    if reference_b64:
        b64, err = generate_image_302_ref(prompt, reference_b64)
        if b64:
            _log_i.info("[image] gemini 图生图成功（有参考图）")
            return b64, None
        return None, err

    # ── Playback: 从缓存读取 ──
    if _CACHE_MODE == "playback":
        cached = _cache.load("chat/completions", IMAGE_REF_MODEL, {"prompt": prompt})
        if cached is not None and cached.get("b64"):
            return cached["b64"], None
        _cache_log.info("[cache] MISS | model=%s | func=generate_image_302 → 调用 API", IMAGE_REF_MODEL)

    # ── 无参考照片：纯文本生图，同样走 gemini-3-pro-image-preview ────────────
    _log_i.info(f"[image] 调用 {IMAGE_REF_MODEL} 纯文本生图")
    full_prompt = (
        f"请严格遵循以下分镜描述，生成一幅电影感的追思纪念场景图片。"
        f"分镜描述：{prompt}。"
        f"风格要求：电影质感、暖色调、16:9 构图、photorealistic, cinematic still, 8K。"
        f"请直接输出生成的图片。"
    )
    try:
        _cache_log.info("[cache] CALL  | model=%s | func=generate_image_302", IMAGE_REF_MODEL)
        resp = PRIMARY_CLIENT.chat.completions.create(
            model=IMAGE_REF_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            stream=False,
        )
    except Exception as exc:
        _log_i.warning(f"[image] {IMAGE_REF_MODEL} 调用失败：{exc}")
        return None, str(exc)

    b64_str, err = _parse_gemini_image_response(resp, "[image_noref]")
    # ── Record: 保存响应 ──
    if _CACHE_MODE in ("record", "playback") and b64_str:
        _cache.save("chat/completions", {"b64": b64_str, "error": err}, IMAGE_REF_MODEL,
                    {"prompt": prompt})
    return b64_str, err


# ── 视频生成（可灵官方 API，kling-v3，首帧模式）────────────────────────────
# 文档：https://www.klingai.com/document-api/apiReference/model/imageToVideo
# 鉴权：JWT（HS256），iss=AccessKeyId，exp=当前+30min
# 提交：POST https://api.klingai.com/v1/videos/image2video
# 查询：GET  https://api.klingai.com/v1/videos/image2video/{task_id}
# 状态：submitted / processing / succeed / failed
# 首帧：body.image = base64 或 HTTPS URL

# ── 302.ai 备用视频接口（Kling 2.6，图生视频 5s）────────────────────────────────
# 文档：https://doc.302.ai/386524568e0
# 提交：POST https://api.302.ai/klingai/m2v_26_image2video_5s  (multipart/form-data)
# 查询：GET  https://api.302.ai/klingai/task/{task_id}/fetch
# 状态：5=排队中 / 10=处理中 / 50=失败退款 / 99=成功
# 视频：data.works[0].resource

_KLING_ACCESS_KEY_ID     = os.getenv("KLING_ACCESS_KEY_ID", "")
_KLING_ACCESS_KEY_SECRET = os.getenv("KLING_ACCESS_KEY_SECRET", "")


def _upload_image_to_public(img_bytes: bytes, ext: str = "png") -> Optional[str]:
    """
    将图片字节上传到图床，返回公开 HTTPS URL。
    链路：tmpfiles.org（48h）→ litterbox.catbox.moe（1h）→ None
    可灵官方 API image 字段只接受 HTTPS URL，不接受 base64。
    """
    import logging as _logging
    _log = _logging.getLogger("llm_client.upload")

    # ── 方案1: tmpfiles.org ──────────────────────────────────────────────────
    try:
        r = _requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (f"frame.{ext}", img_bytes, f"image/{ext}")},
            timeout=30,
        )
        if r.status_code == 200:
            page_url = r.json().get("data", {}).get("url", "")
            if page_url:
                direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                _log.info(f"[upload] tmpfiles.org 成功: {direct_url}")
                return direct_url
        _log.warning(f"[upload] tmpfiles.org 失败 status={r.status_code}: {r.text[:200]}")
    except Exception as e:
        _log.warning(f"[upload] tmpfiles.org 异常: {e}")

    # ── 方案2: litterbox.catbox.moe（1小时有效）─────────────────────────────
    try:
        r = _requests.post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            data={"reqtype": "fileupload", "time": "1h"},
            files={"fileToUpload": (f"frame.{ext}", img_bytes, f"image/{ext}")},
            timeout=30,
        )
        if r.status_code == 200 and r.text.strip().startswith("https://"):
            url = r.text.strip()
            _log.info(f"[upload] litterbox 成功: {url}")
            return url
        _log.warning(f"[upload] litterbox 失败 status={r.status_code}: {r.text[:200]}")
    except Exception as e:
        _log.warning(f"[upload] litterbox 异常: {e}")

    _log.error("[upload] 所有图床均失败")
    return None


def _kling_jwt() -> str:
    """生成可灵官方 API 的 JWT Bearer Token（有效期 30 分钟）"""
    try:
        import jwt as _jwt
    except ImportError:
        raise RuntimeError("需要安装 PyJWT：pip install PyJWT")
    now = int(time.time())
    payload = {
        "iss": _KLING_ACCESS_KEY_ID,
        "exp": now + 1800,   # 30 分钟有效期
        "nbf": now - 5,      # 允许 5 秒时钟误差
    }
    return _jwt.encode(payload, _KLING_ACCESS_KEY_SECRET, algorithm="HS256")

def generate_video_seed(
    prompt: str,
    image_b64_or_url: Optional[str] = None,   # base64 data URL 或公开 HTTPS URL
    negative_prompt: str = "",
    cfg: float = 0.5,
    duration: int = 5,
    poll: bool = True,
    max_wait: int = 600,
    _task_id_only: Optional[str] = None
) -> Dict[str, Any]:
    api_key = _302_API_KEY
    if not api_key or api_key.startswith("sk-填写"):
        return {"error": "未配置 AI302_API_KEY，无法使用 302.ai 视频接口", "source": "302ai"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 只轮询已有任务，跳过提交阶段
    if _task_id_only:
        task_id = _task_id_only
        poll = True
        max_wait = max(max_wait, 1)
    else:
        # 校验必填参数
        if not image_b64_or_url:
            return {"error": "image_b64_or_url 为必填参数（首帧图）", "source": "302ai"}
        # 构建 multipart/form-data 请求体
        body = {
                "model": SEED_VIDEO_GEN_MODEL,
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_b64_or_url}},
                ],
                "ratio": "16:9",
                "duration": 5,
                "resolution": "720p",       # 开发阶段 720p，上线改 "1080p"
                "generate_audio": False,
                "watermark": False,
            }
        try:
            r = _requests.post(_302_VIDEO_SEED_I2V_URL, 
                                   headers=headers, json=body, timeout=60)
            try:
                resp = r.json()
            except Exception:
                return {"error": f"302.ai 响应非 JSON (status={r.status_code})：{r.text[:300]}", "source": "302ai"}
        except Exception as e:
            return {"error": f"302.ai 提交请求异常：{e}", "source": "302ai"}
        if resp.get("status") != 200 or resp.get("result") != 1:
            return {"error": f"302.ai 提交失败：{resp.get('message', str(resp))}", "source": "302ai"}

        task_id = resp.get("id", "")
        if not task_id:
            return {"error": f"302.ai 未返回 task_id：{resp}", "source": "302ai"}
        _llm_log.info("[302ai_i2v] 提交成功 task_id=%s", task_id)

        if not poll:
            return {"task_id": task_id, "status": 5, "source": "302ai"}

    # ── 轮询等待完成 ──
    elapsed = 0
    interval = 10
    while elapsed < max_wait:
        try:
            pr = _requests.get(
                f"{_302_VIDEO_SEED_FETCH_URL}/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"}, 
                timeout=20,
            )
            pd = pr.json()

            # 检查 API 响应级别的状态码（非任务状态码）
            api_status = pd.get("status","")
            if api_status and api_status == "failed":
                msg = pd.get("message", "") or str(pd)
                return {"error": f"302.ai 查询失败（task_id={task_id}）: {msg}", "task_id": task_id, "source": "302ai"}
            elif api_status == "expired":
                return {"error": f"302.ai 任务超时（task_id={task_id}）: {msg}", "task_id": task_id, "source": "302ai"}
            elif api_status == "succeeded":
                video_url = pd.get("content", {}).get("video_url", "")
                if video_url:
                    return {"url": video_url, "task_id": task_id, "status": 99, "source": "302ai"}
                else:
                    return {"error": "302.ai 任务成功但未返回视频 URL", "task_id": task_id, "source": "302ai"}
            else:
                pass 
        except Exception as e:
            return {"error": f"302.ai 查询遇到未知错误：（task_id={task_id}）: {msg}", "task_id": task_id, "source": "302ai"}


        # 只在剩余时间足够时继续等
        elapsed += interval
        if elapsed < max_wait:
            time.sleep(interval)

    if max_wait > interval:
        _llm_log.error("302.ai 等待超时（%ds），task_id=%s", max_wait, task_id)
    else:
        _llm_log.debug("302.ai 单次查询仍在处理，task_id=%s", task_id)
    return {"task_id": task_id, "status": 10, "source": "302ai",
            "error": f"302.ai 等待超时（{max_wait}s），可手动查询 task_id={task_id}"}


def generate_video_302ai_i2v(
    prompt: str,
    image_b64_or_url: Optional[str] = None,   # base64 data URL 或公开 HTTPS URL
    negative_prompt: str = "",
    cfg: float = 0.5,
    duration: int = 5,
    poll: bool = True,
    max_wait: int = 600,
    backend: str = "", 
    _task_id_only: Optional[str] = None,  # 只轮询已有 task_id，跳过提交
) -> Dict[str, Any]:
    """
    通过 302.ai 调用 Kling 2.6 图生视频（5s）。
    接口文档：https://doc.302.ai/386524568e0
    提交：POST https://api.302.ai/klingai/m2v_26_image2video_5s  (multipart/form-data)
    查询：GET  https://api.302.ai/klingai/task/{task_id}/fetch
    状态码：5=排队 / 10=处理中 / 50=失败 / 99=成功
    返回：
      成功 → {"url": "https://...", "task_id": "...", "source": "302ai"}
      排队 → {"task_id": "...", "status": 10, "source": "302ai"}
      失败 → {"error": "...", "source": "302ai"}
    """
    import logging as _log302
    _log302i = _log302.getLogger("llm_client.302ai_i2v")

    if backend == "seed":
        return generate_video_seed(prompt=prompt,
            image_b64_or_url=image_b64_or_url,
            duration=duration,
            poll=poll,
            max_wait=max_wait,
            _task_id_only=_task_id_only)
    

    api_key = _302_API_KEY
    if not api_key or api_key.startswith("sk-填写"):
        return {"error": "未配置 AI302_API_KEY，无法使用 302.ai 视频接口", "source": "302ai"}

    headers = {"Authorization": f"Bearer {api_key}"}

    # 只轮询已有任务，跳过提交阶段
    if _task_id_only:
        task_id = _task_id_only
        poll = True
        max_wait = max(max_wait, 1)
    else:
        # 校验必填参数
        if not image_b64_or_url:
            return {"error": "image_b64_or_url 为必填参数（首帧图）", "source": "302ai"}
        # 构建 multipart/form-data 请求体
        files: Dict[str, Any] = {}
        data: Dict[str, Any] = {
            "prompt": prompt,
            # "negative_prompt": negative_prompt,
            # "cfg": str(cfg),
            # "duration": str(duration),
        }

        if image_b64_or_url:
            if image_b64_or_url.startswith("data:"):
                # base64 data URL → 解码为字节，作为文件上传
                try:
                    header_part, b64_part = image_b64_or_url.split(",", 1)
                    mime = header_part.split(":")[1].split(";")[0]
                    ext = mime.split("/")[-1] if "/" in mime else "png"
                    img_bytes = base64.b64decode(b64_part)
                    files["input_image"] = (f"frame.{ext}", img_bytes, mime)
                except Exception as e:
                    return {"error": f"base64 图片解析失败：{e}", "source": "302ai"}
            else:
                # 已是 HTTPS URL，直接作为文本字段传入
                        # HTTPS URL — 下载图片后以 multipart/form-data 发送
                try:
                    img_resp = _requests.get(image_b64_or_url, timeout=30)
                    img_bytes = img_resp.content
                    mime = img_resp.headers.get("Content-Type", "image/png")
                    files = {"input_image": ("frame.png", img_bytes, mime)}
                except Exception as e:
                    return {"error": f"图片下载失败: {e}", "source": "302ai"}

        try:
            r = _requests.post(
                _302_VIDEO_I2V_URL,
                headers=headers,
                files=files if files else None,
                data=data,
                timeout=60,
            )
            try:
                resp = r.json()
            except Exception:
                return {"error": f"302.ai 响应非 JSON (status={r.status_code})：{r.text[:300]}", "source": "302ai"}
        except Exception as e:
            return {"error": f"302.ai 提交请求异常：{e}", "source": "302ai"}
        if resp.get("status") != 200 or resp.get("result") != 1:
            return {"error": f"302.ai 提交失败：{resp.get('message', str(resp))}", "source": "302ai"}

        task_id = resp.get("data", {}).get("task", {}).get("id", "")
        if not task_id:
            return {"error": f"302.ai 未返回 task_id：{resp}", "source": "302ai"}

        _llm_log.info("[302ai_i2v] 提交成功 task_id=%s", task_id)

        if not poll:
            return {"task_id": task_id, "status": 5, "source": "302ai"}

    # ── 轮询等待完成 ──
    elapsed = 0
    interval = 10
    while elapsed < max_wait:
        try:
            pr = _requests.get(
                f"{_302_VIDEO_FETCH_URL}/{task_id}/fetch",
                headers=headers,
                timeout=20,
            )
            pd = pr.json()

            # 检查 API 响应级别的状态码（非任务状态码）
            api_status = pd.get("status") or pd.get("code")
            if api_status and api_status != 200:
                msg = pd.get("message", "") or str(pd)
                return {"error": f"302.ai 查询失败（task_id={task_id}）: {msg}", "task_id": task_id, "source": "302ai"}

            data = pd.get("data", {})
            cur_status = data.get("status")
            if cur_status is None:
                # 302.ai 可能返回 task_id 不存在的错误
                msg = data.get("message", "") or pd.get("message", "")
                if msg:
                    return {"error": f"302.ai 查询无结果: {msg}", "task_id": task_id, "source": "302ai"}
                return {"error": f"302.ai 返回未知响应: {str(pd)[:200]}", "task_id": task_id, "source": "302ai"}

            if cur_status == 99:
                works = pd.get("data", {}).get("works", [])
                if works:
                    first_work = works[0]
                    res_obj = first_work.get("resource", {})
                    video_url = res_obj.get("resource", "") if isinstance(res_obj, dict) else (res_obj or "")
                    if video_url:
                        return {"url": video_url, "task_id": task_id, "status": 99, "source": "302ai"}
                return {"error": "302.ai 任务成功但未返回视频 URL", "task_id": task_id, "source": "302ai"}
            elif cur_status == 50:
                return {"error": "302.ai 任务失败（已自动退款）", "task_id": task_id, "source": "302ai"}
            # status 5(排队) / 10(处理中) → 继续等待
        except Exception:
            pass

        # 只在剩余时间足够时继续等
        elapsed += interval
        if elapsed < max_wait:
            time.sleep(interval)

    if max_wait > interval:
        _llm_log.error("302.ai 等待超时（%ds），task_id=%s", max_wait, task_id)
    else:
        _llm_log.debug("302.ai 单次查询仍在处理，task_id=%s", task_id)
    return {"task_id": task_id, "status": 10, "source": "302ai",
            "error": f"302.ai 等待超时（{max_wait}s），可手动查询 task_id={task_id}"}


def generate_video_kling(
    prompt: str,
    image_url: Optional[str] = None,   # base64 data URL 或 HTTPS URL（首帧图）
    image_tail_url: Optional[str] = None,  # 尾帧图（可选，kling-v2-6 支持）
    negative_prompt: str = "",
    duration: int = 5,
    mode: str = "pro",
    aspect_ratio: str = "16:9",
    sound: str = "off",
    poll: bool = True,
    max_wait: int = 600,
    _task_id_only: Optional[str] = None,  # 只轮询已有 task_id，跳过提交
) -> Dict[str, Any]:
    """
    调用可灵官方 API（kling-v3）生成视频。
    若 KLING_ACCESS_KEY_ID / SECRET 未配置，或官方 API 调用失败，
    自动 fallback 到 302.ai（m2v_26_image2video_5s）。

    image_url      : 首帧图，base64 data URL 或 HTTPS URL。
    image_tail_url : 尾帧图（可选），同格式。
    poll           : True 时轮询等待完成并返回视频 URL；False 立即返回 task_id。
    返回:
      成功 → {"url": "https://...", "task_id": "...", "source": "kling"|"302ai"}
      排队 → {"task_id": "...", "status": ..., "source": ...}
      失败 → {"error": "..."}
    """
    import logging as _logv
    _log_v = _logv.getLogger("llm_client.video")

    # ── 0. 只轮询已有任务，跳过提交 ─────────────────────────────────────────
    if _task_id_only:
        if not _KLING_ACCESS_KEY_ID or not _KLING_ACCESS_KEY_SECRET:
            return generate_video_302ai_i2v(
                prompt="", _task_id_only=_task_id_only, poll=True, max_wait=max(max_wait, 1),
            )
        # 走可灵官方轮询（跳到轮询循环，task_id 已知）
        poll = True
        max_wait = max(max_wait, 1)

    # ── Playback: 从缓存读取 ──
    if _CACHE_MODE == "playback":
        img_hash = ""
        if image_url:
            img_hash = hashlib.sha256(image_url[:200].encode()).hexdigest()[:8]
        cached = _cache.load("video/generate", "kling-v3",
                             {"prompt": prompt, "img_hash": img_hash})
        if cached is not None:
            return cached
        _cache_log.info("[cache] MISS | model=kling-v3 | func=generate_video_kling → 调用 API")

    # ── 1. 若未配置可灵官方 key，直接走 302.ai ───────────────────────────────
    if not _KLING_ACCESS_KEY_ID or not _KLING_ACCESS_KEY_SECRET:
        _log_v.info("[video] 可灵官方 key 未配置，直接使用 302.ai 备用接口")
        _cache_log.info("[cache] CALL  | model=302.ai(kling2.6) | func=generate_video_kling")
        result = generate_video_302ai_i2v(
            prompt=prompt,
            image_b64_or_url=image_url,
            duration=duration,
            poll=poll,
            max_wait=max_wait,
        )
        # ── Record: 保存响应 ──
        if _CACHE_MODE in ("record","playback") and "url" in result:
            img_hash = ""
            if image_url:
                img_hash = hashlib.sha256(image_url[:200].encode()).hexdigest()[:8]
            _cache.save("video/generate", result, "kling-v3",
                        {"prompt": prompt, "img_hash": img_hash})
        return result

    # ── 2. 尝试可灵官方 API ──────────────────────────────────────────────────
    try:
        token = _kling_jwt()
    except Exception as e:
        _log_v.warning(f"[video] JWT 生成失败，fallback 到 302.ai：{e}")
        return generate_video_302ai_i2v(
            prompt=prompt, image_b64_or_url=image_url,
            poll=poll, max_wait=max_wait,
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 构建请求体（kling-v3 接口规范）
    body: Dict[str, Any] = {
        "model_name": "kling-v3",
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "duration": str(duration),       # "5" 或 "10"
        "mode": mode,                    # "std" 或 "pro"
        "aspect_ratio": aspect_ratio,
        "sound": sound,                  # "on" / "off"
        "callback_url": "",
        "external_task_id": "",
    }

    def _resolve_image(raw_url: str) -> Optional[str]:
        """base64 data URL → 图床 HTTPS URL；HTTPS URL 直接返回；相对路径 → 读取本地文件上传图床；失败返回 None"""
        if raw_url.startswith("data:"):
            try:
                header_part, b64_part = raw_url.split(",", 1)
                mime = header_part.split(":")[1].split(";")[0]
                ext  = mime.split("/")[-1] if "/" in mime else "png"
                img_bytes = base64.b64decode(b64_part)
                return _upload_image_to_public(img_bytes, ext)
            except Exception as _e:
                _log_v.warning(f"[video] base64 解析失败：{_e}")
                return None
        if raw_url.startswith(("http://", "https://")):
            return raw_url  # 已是完整 URL
        # 相对路径 → 解析为本地文件并上传图床
        if raw_url.startswith("/"):
            try:
                # /api/outputs/... → DATA_DIR/outputs/...
                rel = raw_url.replace("/api/outputs/", "", 1)
                full_path = str(DATA_DIR / "outputs" / rel)
                if os.path.isfile(full_path):
                    ext = os.path.splitext(full_path)[1].lstrip(".") or "png"
                    with open(full_path, "rb") as f:
                        img_bytes = f.read()
                    return _upload_image_to_public(img_bytes, ext)
                else:
                    _log_v.warning(f"[video] 本地文件不存在：{full_path}")
            except Exception as _e:
                _log_v.warning(f"[video] 相对路径解析失败：{_e}")
        return None

    # 首帧图
    if image_url:
        public_image = _resolve_image(image_url)
        if not public_image:
            _log_v.warning("[video] 首帧图处理失败，fallback 到 302.ai")
            return generate_video_302ai_i2v(
                prompt=prompt, image_b64_or_url=image_url,
                duration=duration, poll=poll, max_wait=max_wait,
            )
        body["image"] = public_image

    # 尾帧图（可选）
    if image_tail_url:
        public_tail = _resolve_image(image_tail_url)
        if public_tail:
            body["image_tail"] = public_tail
        else:
            _log_v.warning("[video] 尾帧图处理失败，忽略 image_tail 字段继续提交")

    submit_url = f"{_KLING_OFFICIAL_BASE}/v1/videos/image2video"
    if _task_id_only:
        # 跳过提交，直接进入轮询
        task_id = _task_id_only
    else:
        _cache_log.info("[cache] CALL  | model=kling-v3 | func=generate_video_kling")
        try:
            r = _requests.post(submit_url, headers=headers, json=body, timeout=60)
            try:
                resp_data = r.json()
            except Exception:
                _log_v.warning("[video] 官方 API 响应非 JSON，fallback 到 302.ai")
                return generate_video_302ai_i2v(
                    prompt=prompt, image_b64_or_url=image_url,
                    duration=duration, poll=poll, max_wait=max_wait,
                )
        except Exception as e:
            _log_v.warning(f"[video] 官方 API 请求异常，fallback 到 302.ai：{e}")
            return generate_video_302ai_i2v(
                prompt=prompt, image_b64_or_url=image_url,
                duration=duration, poll=poll, max_wait=max_wait,
            )

        if resp_data.get("code", -1) != 0:
            _log_v.warning(f"[video] 官方 API 提交失败 code={resp_data.get('code')}，fallback 到 302.ai")
            return generate_video_302ai_i2v(
                prompt=prompt, image_b64_or_url=image_url,
                duration=duration, poll=poll, max_wait=max_wait,
            )

        task_id = resp_data.get("data", {}).get("task_id", "")
        if not task_id:
            _log_v.warning("[video] 官方 API 未返回 task_id，fallback 到 302.ai")
            return generate_video_302ai_i2v(
                prompt=prompt, image_b64_or_url=image_url,
                duration=duration, poll=poll, max_wait=max_wait,
            )

        if not poll:
            return {"task_id": task_id, "status": "submitted", "source": "kling"}

    # ── 轮询等待完成 ──────────────────────────────────────────────────────────
    poll_url  = f"{_KLING_OFFICIAL_BASE}/v1/videos/image2video/{task_id}"
    elapsed   = 0
    interval  = 10
    while elapsed < max_wait:
        try:
            token = _kling_jwt()
            pr = _requests.get(
                poll_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            pd = pr.json()
            if pd.get("code", -1) != 0:
                return {"error": f"查询失败：{pd.get('message','')}", "task_id": task_id}

            task_data   = pd.get("data", {})
            task_status = task_data.get("task_status", "")

            if task_status == "succeed":
                videos = task_data.get("task_result", {}).get("videos", [])
                video_url = videos[0].get("url", "") if videos else ""
                if video_url:
                    result = {"url": video_url, "task_id": task_id, "source": "kling"}
                    if _CACHE_MODE in ("record", "playback"):
                        _cache.save("video/generate", result, "kling-v3",
                                    {"prompt": prompt})
                    return result
                return {"error": "任务完成但未返回视频 URL", "task_id": task_id, "source": "kling"}

            elif task_status == "failed":
                reason = task_data.get("task_status_msg", "未知原因")
                return {"error": f"任务失败：{reason}", "task_id": task_id, "source": "kling"}

            # submitted / processing → 继续等待
        except Exception:
            pass

        elapsed += interval
        if elapsed < max_wait:
            time.sleep(interval)

    if max_wait > interval:
        _llm_log.error("Kling 等待超时（%ds），task_id=%s", max_wait, task_id)
    else:
        _llm_log.debug("Kling 单次查询仍在处理，task_id=%s", task_id)
    return {"task_id": task_id, "status": "processing", "source": "kling",
            "error": f"等待超时（{max_wait}s），可手动轮询 task_id={task_id}"}


# 兼容旧调用名（studio.py 等地方仍用 generate_video_302）
generate_video_302 = generate_video_kling



# ── 分镜专用结构化调用（MV04，使用 gpt-4o） ──────────────────────────────────

def call_storyboard(system_prompt: str, user_content: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    分镜制作专用函数，使用 gpt-4o 优先队列。
    gpt-4o 支持 JSON mode，速度更快，结构化输出更稳定。
    """
    # ── Playback ──
    if _CACHE_MODE == "playback" or use_cache:
        cached = _cache.load("chat/completions", STORYBOARD_MODEL, {"system": system_prompt, "user": user_content})
        if cached is not None:
            return cached
        _cache_log.info("[cache] MISS | model=%s | func=call_storyboard → 调用 API", STORYBOARD_MODEL)

    last_error: Optional[str] = None
    _had_fallback = False
    for model_name, client in _storyboard_model_queue():
        use_json_mode = _supports_json_mode(model_name)
        sys_content   = system_prompt if use_json_mode else _json_system_hint(system_prompt)
        for attempt in range(1, 4):
            try:
                _cache_log.info("[cache] CALL  | model=%s | func=call_storyboard", model_name)
                kwargs: Dict[str, Any] = dict(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": sys_content},
                        {"role": "user",   "content": user_content},
                    ],
                    temperature=0.3,
                )
                if use_json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)
                raw = response.choices[0].message.content or "{}"
                if not use_json_mode:
                    raw = _extract_json(raw)
                result = json.loads(raw)
                if _had_fallback:
                    _llm_log.info("[llm] %s 降级到 %s，storyboard 成功", STORYBOARD_MODEL, model_name)
                if _CACHE_MODE in ("record", "playback"):
                    _cache.save("chat/completions", result, model_name,
                                {"system": system_prompt, "user": user_content})
                return result
            except Exception as exc:
                last_error = f"{model_name}: {exc}"
                if attempt >= 3:
                    _llm_log.warning("[llm] %s 连续3次重试均失败，storyboard: %s", model_name, exc)
                    _had_fallback = True
                if attempt < 3:
                    time.sleep(1)
    return {"error": True, "message": last_error or "Unknown error"}
