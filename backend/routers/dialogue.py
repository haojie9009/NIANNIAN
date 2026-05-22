# backend/routers/dialogue.py — 数字人对话（聊天记录分析 + 多轮扮演对话）
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from services import service_manager as sm
from services import session_store

router = APIRouter(prefix="/dialogue", tags=["dialogue"])


def _ensure_session(sid: Optional[str]) -> Dict[str, Any]:
    """没有 session 就创建一个空 session"""
    if sid:
        s = session_store.get(sid)
        if s is not None:
            return s
    new_sid = session_store.create_session({})
    return session_store.require(new_sid)


@router.get("/state/{sid}")
def get_state(sid: str) -> Dict[str, Any]:
    s = session_store.get(sid)
    if not s:
        raise HTTPException(404, "session not found")
    d = s["dialogue"]
    return {
        "session_id":       s["session_id"],
        "persona_dna":      d.get("persona_dna"),
        "persona_name":     d.get("persona_name", ""),
        "persona_override": d.get("persona_override", ""),
        "message_count":    d.get("message_count", 0),
        "history":          d.get("history", []),
    }


@router.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    target_name: str = Form(""),
    role_desc:   str = Form(""),
    session_id:  str = Form(""),
) -> Dict[str, Any]:
    """上传聊天记录文件 → 解析 → AI 风格分析 → 写入 session.dialogue"""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")

    messages = sm.parse_chat_file(raw, file.filename or "chat.txt", target_name)
    if len(messages) < 3:
        return {
            "error": True,
            "message": f"消息数量不足（{len(messages)} 条），请检查文件或姓名筛选",
            "message_count": len(messages),
        }

    result = sm.analyze_chat_style(messages, target_name or "目标人物", role_desc)
    if isinstance(result, dict) and result.get("error"):
        return {"error": True, "message": result.get("message", "分析失败")}

    s = _ensure_session(session_id or None)
    d = s["dialogue"]
    d["persona_dna"]   = result
    d["persona_name"]  = target_name.strip() or "TA"
    d["message_count"] = len(messages)
    d["history"]       = []   # 重新分析则清空历史
    d["persona_override"] = role_desc.strip() if role_desc.strip() else ""

    return {
        "ok": True,
        "session_id":     s["session_id"],
        "persona_dna":    result,
        "persona_name":   d["persona_name"],
        "message_count":  len(messages),
    }


class PersonaUpdateReq(BaseModel):
    session_id: str
    new_input:  str = ""
    clear:      bool = False


@router.post("/persona/update")
def persona_update(req: PersonaUpdateReq) -> Dict[str, Any]:
    try:
        s = session_store.require(req.session_id)
    except KeyError:
        raise HTTPException(404, "session not found")
    d = s["dialogue"]
    if not d.get("persona_dna"):
        raise HTTPException(400, "请先上传聊天记录完成风格分析")
    if req.clear:
        d["persona_override"] = ""
        return {"ok": True, "persona_override": ""}
    if not req.new_input.strip():
        raise HTTPException(400, "请输入修改/补充内容")
    merged = sm.merge_persona(d["persona_dna"], d.get("persona_override", ""), req.new_input.strip())
    d["persona_override"] = merged
    return {"ok": True, "persona_override": merged}


class ChatReq(BaseModel):
    session_id: str
    message:    str


@router.post("/chat")
def chat(req: ChatReq) -> Dict[str, Any]:
    try:
        s = session_store.require(req.session_id)
    except KeyError:
        raise HTTPException(404, "session not found")
    d = s["dialogue"]
    if not d.get("persona_dna"):
        raise HTTPException(400, "请先上传聊天记录完成风格分析")
    msg = (req.message or "").strip()
    if not msg:
        raise HTTPException(400, "empty message")
    d["history"].append({"role": "user", "content": msg})
    reply = sm.dialogue_reply(d["persona_dna"], d.get("persona_name", "TA"),
                              d.get("persona_override", ""), d["history"])
    d["history"].append({"role": "assistant", "content": reply})
    return {"reply": reply, "history": d["history"]}


class ResetReq(BaseModel):
    session_id: str
    clear_persona: bool = False


@router.post("/reset")
def reset(req: ResetReq) -> Dict[str, Any]:
    try:
        s = session_store.require(req.session_id)
    except KeyError:
        raise HTTPException(404, "session not found")
    d = s["dialogue"]
    d["history"] = []
    if req.clear_persona:
        d["persona_dna"] = None
        d["persona_name"] = ""
        d["persona_override"] = ""
        d["message_count"] = 0
    return {"ok": True}
