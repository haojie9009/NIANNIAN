# backend/services/session_store.py
# 内存级 session 存储，重启后从磁盘恢复；生产可替换为 Redis。
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import gate_manager
from logger import svc_logger

_LOCK = threading.RLock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}

# 持久化目录：优先读环境变量 NIAN_DATA_DIR，否则用 backend/data/sessions/
_DATA_DIR = Path(os.environ.get("NIAN_DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "sessions"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(sid: str) -> Path:
    return _DATA_DIR / f"{sid}.json"


def _save(sid: str) -> None:
    """将单个 session 序列化写盘（在 _LOCK 内调用）。"""
    s = _SESSIONS.get(sid)
    if s is None:
        return
    try:
        tmp = _session_path(sid).with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(_session_path(sid))
    except Exception as e:
        svc_logger.exception(f"[session_store] failed to save session {sid}: {e}")


def load_all() -> int:
    """启动时从磁盘加载所有 session，返回加载数量。"""
    loaded = 0
    now = time.time()
    with _LOCK:
        for f in _DATA_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("session_id")
                if not sid:
                    continue
                # 跳过已过期的 session
                if now - data.get("updated_at", 0) > _TTL_SEC:
                    f.unlink(missing_ok=True)
                    continue
                _SESSIONS[sid] = data
                loaded += 1
            except Exception as e:
                svc_logger.exception(f"[session_store] failed to load session from {f}: {e}")
    return loaded


# session 自动清理时间（7 天未操作即过期）
_TTL_SEC = 7 * 24 * 3600

# 内存兜底上限：即使未过期，也只保留最近 10 个
_MAX_SESSIONS = 10


def create_session(form_data: Optional[Dict[str, Any]] = None) -> str:
    sid = uuid.uuid4().hex
    with _LOCK:
        _SESSIONS[sid] = {
            "session_id":    sid,
            "created_at":    time.time(),
            "updated_at":    time.time(),
            "form_data":     dict(form_data or {}),
            "assets":        [],
            "chat_history":  [],
            "mv_outputs":    {},
            "preview_text":  "",
            "gate":          gate_manager.new_state(),
            "pipeline_state": {
                m: {"status": "pending", "duration_sec": None, "error": None}
                for m in gate_manager.GATE_ORDER
            },
            "ds_chat":       [],   # 深度搜索对话
            "ds_result":     None,
            # 数字人对话子状态（独立于 memorial 影像建档流程）
            "dialogue": {
                "persona_dna":      None,   # dict：聊天分析出的语言风格
                "persona_name":     "",
                "persona_override": "",     # 人设编辑器中累积的描述
                "message_count":    0,      # 已分析的消息条数
                "history":          [],     # [{role, content}]
            },
            "audio":          {},   # {"tts_segments": [...], "bgm": {...}}
        }
        _save(sid)
    return sid


def _load_from_disk(sid: str) -> Optional[Dict[str, Any]]:
    """从磁盘读取单个 session（不经过内存）。"""
    p = _session_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_disk_ids() -> list:
    """列出磁盘上所有 session ID。"""
    return [f.stem for f in _DATA_DIR.glob("*.json") if f.stem]


def get(sid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _SESSIONS.get(sid)


def require(sid: str) -> Dict[str, Any]:
    s = get(sid)
    if not s:
        raise KeyError(f"session not found: {sid}")
    return s


def update(sid: str, **patches: Any) -> Dict[str, Any]:
    with _LOCK:
        s = _SESSIONS.get(sid)
        if s is None:
            raise KeyError(f"session not found: {sid}")
        for k, v in patches.items():
            s[k] = v
        s["updated_at"] = time.time()
        _save(sid)
        return s


def patch_form(sid: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        s = _SESSIONS.get(sid)
        if s is None:
            raise KeyError(f"session not found: {sid}")
        for k, v in fields.items():
            if v not in (None, ""):
                s["form_data"][k] = v
        s["updated_at"] = time.time()
        _save(sid)
        return s


def gc() -> int:
    """清理过期 session + 超过上限时淘汰最旧的（内存 + 磁盘）"""
    now = time.time()
    removed = 0
    with _LOCK:
        # 1) TTL 过期清理
        expired = [
            sid for sid, s in _SESSIONS.items()
            if now - s["updated_at"] > _TTL_SEC
        ]
        for sid in expired:
            del _SESSIONS[sid]
            _session_path(sid).unlink(missing_ok=True)
            removed += 1

        # 2) 数量上限兜底：按 updated_at 排序，淘汰最旧的
        if len(_SESSIONS) > _MAX_SESSIONS:
            ordered = sorted(_SESSIONS.values(), key=lambda s: s["updated_at"])
            to_evict = len(_SESSIONS) - _MAX_SESSIONS
            for s in ordered[:to_evict]:
                sid = s["session_id"]
                del _SESSIONS[sid]
                _session_path(sid).unlink(missing_ok=True)
                removed += 1

    return removed


def list_ids() -> list:
    with _LOCK:
        return list(_SESSIONS.keys())
