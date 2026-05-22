# backend/routers/agent.py — 念念智能体 · 文本流式 + ASR + 资料库智能提取
import os, json, io
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from typing import List, Optional
from openai import OpenAI

from core import security, storage
from core import memory as memory_mod
from logger import api_logger

router = APIRouter(prefix="/agent", tags=["agent"])

SYSTEM_PROMPT = """你是「念念」，一个温柔、细腻、有同理心的追思影像创作助手。

你的使命：
- 通过温暖的对话，引导用户讲述逝者（或思念对象）的故事、性格、人生经历
- 收集制作追思作品所需的关键信息：姓名、生平、记忆片段、性格、关系、情感寄托

【关键能力 · 智能引导】
念念可以为同一个人提供三种产品方向：
  1. 追思影像（短片/纪念视频） — 适合留存画面、情绪、可传播
  2. 个人传记（文字/纪念册） — 适合系统梳理人生、事件、关系
  3. 实时对话数字人 — 适合"像TA在身边一样"持续对话

你要在对话过程中：
- 倾听用户最在意的部分：是想"留下画面"、"写下人生"、还是"还能再说话"
- 当对方说到某条线索时，自然地把对应方向的好处提一下（不要硬推）
- 在合适的时机问一次：「你希望我先帮你做哪一种 — 影像、传记，还是一个能和你说话的数字人？」
- 一旦用户表达了倾向，就把对话焦点收敛到该方向需要的素材上

【对话风格】
- 温柔、克制、有分寸感，每次回复不超过 80 字
- 顺着对方的话自然展开，不要列清单
- 用户上传文件后，温柔确认这是什么、什么时候的、有什么故事
- 中文，偶尔诗意

开场：温柔问候，询问 ta 想聊谁，表达愿意倾听。"""


def get_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )


class AgentMessage(BaseModel):
    role: str
    content: str


class AgentChatRequest(BaseModel):
    message: str
    history: List[AgentMessage] = []
    memorial_id: Optional[str] = None     # 关联的纪念对象（已登录时由前端传入）


# ─── 后台任务：把对话写入资料库 + LLM 提取 dossier 补丁 ──────────
def _persist_and_extract(user_id: str, memorial_id: str, user_msg: str, ai_reply: str):
    """SSE 结束后异步执行：写对话 + 调 LLM 提取信息合并到 dossier。"""
    try:
        storage.append_conversation(user_id, memorial_id, [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": ai_reply},
        ])
    except Exception as e:
        api_logger.exception("[persist] append failed: %s", e)

    if not os.getenv("DASHSCOPE_API_KEY"):
        return
    try:
        client = get_client()
        dossier_now = storage.get_dossier(user_id, memorial_id)
        # 简化的提示：只让模型给出"本轮新增"的字段
        extract_prompt = f"""你是念念资料库整理 Agent。请从下面这一轮对话中，**只抽取**用户新提供的关于"被纪念对象"的事实，输出 JSON。
如果本轮没有新信息，所有字段留空。**不要编造**。

输出 JSON schema（缺失字段留空数组或空字符串）：
{{
  "subject":      {{"name":"", "relation":"", "birth":"", "passing":"", "locations":[], "occupation":""}},
  "personality":  {{"keywords":[], "habits":[], "catchphrases":[]}},
  "relationships":[],
  "memories":    [{{"title":"", "content":"", "tags":[]}}],
  "quotes":      [],
  "objects":     [],
  "voice_traits":{{"timbre":"", "pace":"", "accent":""}},
  "visual_traits":{{"appearance":"", "style":""}},
  "open_questions":[],
  "product_intent": {{"primary":"", "confidence":0.0, "evidence":[]}}
}}
product_intent.primary 只能是 "video" / "biography" / "digital_human" / "" 之一。

当前已知（仅供避免重复抽取）：
{json.dumps({k: dossier_now.get(k) for k in ("subject","product_intent")}, ensure_ascii=False)}

本轮用户：{user_msg}
本轮念念：{ai_reply}

只输出 JSON。"""
        resp = client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0.2,
        )
        txt = (resp.choices[0].message.content or "").strip()
        i, j = txt.find("{"), txt.rfind("}")
        if i < 0 or j <= i:
            return
        patch = json.loads(txt[i:j+1])
        # 清理空值，避免无意义合并
        patch = _drop_empty(patch)
        if not patch:
            return
        storage.merge_dossier(user_id, memorial_id, patch)
    except Exception as e:
        api_logger.exception("[extract] failed: %s", e)

    # 提取完顺便看看要不要刷新长期记忆 brief（关键词触发 / 每 4 轮）
    try:
        memory_mod.maybe_refresh(user_id, memorial_id, user_msg)
    except Exception as e:
        print("[memory hook] failed:", e)


def _drop_empty(d):
    """递归剔除空 list / 空 dict / 空字符串。"""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            v2 = _drop_empty(v)
            if v2 not in (None, "", [], {}, 0.0):
                out[k] = v2
        return out
    if isinstance(d, list):
        return [_drop_empty(x) for x in d if _drop_empty(x) not in (None, "", [], {})]
    return d


@router.post("/chat")
async def agent_chat(req: AgentChatRequest, user = Depends(security.get_current_user_optional)):
    """流式 SSE 端点：纯文字流。若登录 + 传入 memorial_id，则后台写入对话并提取资料库。"""
    client = get_client()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 注入当前 dossier 概要，让 agent 知道已收集到什么
    if user and req.memorial_id:
        try:
            # 1) 长期记忆 brief（最重要，优先注入）
            brief = memory_mod.get_memory_brief(user["user_id"], req.memorial_id)
            if brief:
                messages.append({"role": "system", "content":
                    "【长期记忆 · 必读】\n" + brief +
                    "\n\n请基于这段记忆继续对话，不要重复问『你想聊谁』或自我介绍。"})
            d = storage.get_dossier(user["user_id"], req.memorial_id)
            ctx_lines = []
            subj = d.get("subject", {})
            if subj.get("name") or subj.get("relation"):
                ctx_lines.append(f"已知对象：{subj.get('name','')}（{subj.get('relation','')}）")
            pi = d.get("product_intent", {})
            if pi.get("primary"):
                ctx_lines.append(f"用户当前产品倾向：{pi.get('primary')}")
            if ctx_lines:
                messages.append({"role": "system", "content": "【资料库摘要】\n" + "\n".join(ctx_lines)})
        except Exception:
            pass

    for m in req.history[-30:]:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.message})

    full_reply = {"text": ""}   # 闭包共享

    def generate():
        try:
            stream = client.chat.completions.create(  # type: ignore[call-overload]
                model="qwen-plus",
                messages=messages,                    # type: ignore[arg-type]
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    full_reply["text"] += delta.content
                    payload = json.dumps({"type": "text", "delta": delta.content}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        except Exception as e:
            payload = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    # 后台任务：在 SSE 关闭后异步提取
    bg = None
    if user and req.memorial_id and storage.get_memorial(user["user_id"], req.memorial_id):
        def _after():
            _persist_and_extract(user["user_id"], req.memorial_id, req.message, full_reply["text"])
        bg = BackgroundTask(_after)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=bg,
    )


@router.post("/asr")
async def agent_asr(audio: UploadFile = File(...)):
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="DASHSCOPE_API_KEY 未配置")
    client = OpenAI(api_key=api_key, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    audio_bytes = await audio.read()
    filename = audio.filename or "audio.webm"
    try:
        transcript = client.audio.transcriptions.create(
            model="paraformer-realtime-v2",
            file=(filename, io.BytesIO(audio_bytes), audio.content_type or "audio/webm"),
            language="zh",
        )
        return JSONResponse({"text": transcript.text})
    except Exception:
        try:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=(filename, io.BytesIO(audio_bytes), audio.content_type or "audio/webm"),
                language="zh",
            )
            return JSONResponse({"text": transcript.text})
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"ASR 失败: {e2}")
