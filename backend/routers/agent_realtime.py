# backend/routers/agent_realtime.py — 念念 · Qwen-Omni-Realtime WebSocket 代理
# v2: 注入 dossier+对话记忆, 关闭服务端 VAD, 断开时持久化对话+提取档案
import os
import json
import asyncio
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except Exception:
    websockets = None
    ws_connect = None

from core import security, storage
from core import memory as memory_mod
from logger import api_logger


router = APIRouter(prefix="/agent", tags=["agent-realtime"])

UPSTREAM_URL = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model=qwen3.5-omni-plus-realtime"

BASE_INSTRUCTIONS = (
    "你是『念念』，一个温柔、细腻、有同理心的追思影像创作助手。"
    "用温暖的对话引导用户讲述思念之人的故事、性格、人生经历；"
    "语气克制、有分寸，像贴心的倾听者。每次回复控制在 80 字以内，"
    "主动追问一个具体细节。使用中文。"
    "\n\n【非常重要】"
    "用户可能慢慢说，中间会有停顿（最长 7 秒），请耐心等待 ta 说完整段再回复，不要打断。"
    "如果用户只是简短确认（嗯、好、对），不要长篇大论。"
    "永远不要重复自我介绍。如果上下文已经在聊某个人，直接接着聊，"
    "**不要再问『今天想聊谁』或者『你是谁』**。"
)


def _build_context_brief(user_id: str, memorial_id: str) -> str:
    lines = []
    # 0) 长期记忆 brief（qwen-plus 精炼，优先级最高）
    try:
        brief = memory_mod.get_memory_brief(user_id, memorial_id)
        if brief:
            lines.append("【长期记忆 · 必读】")
            lines.append(brief)
    except Exception:
        pass
    try:
        d = storage.get_dossier(user_id, memorial_id) or {}
        subj = d.get("subject") or {}
        if subj.get("name") or subj.get("relation"):
            lines.append(f"【正在聊的对象】{subj.get('name','')}（{subj.get('relation','')}）")
        if subj.get("birth") or subj.get("passing"):
            lines.append(f"  · {subj.get('birth','')} ~ {subj.get('passing','')}")
        pers = (d.get("personality") or {}).get("keywords") or []
        if pers:
            lines.append(f"【性格关键词】{','.join(pers[:8])}")
        mems = d.get("memories") or []
        if mems:
            lines.append("【已记录的记忆片段】")
            for m in mems[-3:]:
                t = m.get("title") or ""
                c = (m.get("content") or "")[:40]
                lines.append(f"  · {t}：{c}")
        quotes = d.get("quotes") or []
        if quotes:
            lines.append(f"【金句】{' / '.join(quotes[:3])}")
        pi = (d.get("product_intent") or {}).get("primary")
        if pi:
            lines.append(f"【用户产品倾向】{pi}")
    except Exception:
        pass
    try:
        # 短期记忆：最近 4 轮（≈8 条）
        convs = storage.read_conversations(user_id, memorial_id, limit=8) or []
        if convs:
            lines.append("【最近的对话（请基于此继续）】")
            for c in convs:
                role = "用户" if c.get("role") == "user" else "念念"
                txt = (c.get("content") or "").strip().replace("\n", " ")
                if len(txt) > 80:
                    txt = txt[:80] + "..."
                lines.append(f"  {role}：{txt}")
    except Exception:
        pass
    if not lines:
        return ""
    return ("\n\n【对话记忆 · 必读】\n" + "\n".join(lines) +
            "\n\n请基于以上记忆继续这段对话，不要从头开始问，不要重复自我介绍。")


def _persist_realtime_turns(user_id: str, memorial_id: str, turns: list):
    if not turns:
        return
    try:
        storage.append_conversation(user_id, memorial_id, turns)
    except Exception as e:
        print("[realtime persist] failed:", e)
    try:
        from .agent import _persist_and_extract
        last_user, last_ai = "", ""
        for t in turns:
            if t.get("role") == "user":
                last_user = t.get("content", "")
            elif t.get("role") == "assistant":
                last_ai = t.get("content", "")
        if last_user and last_ai:
            _persist_and_extract(user_id, memorial_id, last_user, last_ai)
        # 关键词 / 每 4 轮触发长期记忆刷新
        if last_user:
            try:
                memory_mod.maybe_refresh(user_id, memorial_id, last_user)
            except Exception as e:
                print("[realtime memory] failed:", e)
    except Exception as e:
        print("[realtime extract] failed:", e)


@router.websocket("/realtime")
async def realtime_proxy(client_ws: WebSocket):
    await client_ws.accept()
    qp = client_ws.query_params
    mid = (qp.get("mid") or "").strip()
    token = (qp.get("token") or "").strip()

    user: Optional[dict] = None
    if token:
        try:
            payload = security.decode_token(token)
            uid = payload.get("sub") or payload.get("user_id")
            if payload and uid:
                user = {"user_id": uid}
        except Exception:
            user = None

    can_persist = False
    if user and mid:
        try:
            if storage.get_memorial(user["user_id"], mid):
                can_persist = True
        except Exception:
            pass

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        await client_ws.send_json({"type": "error", "message": "服务端未配置 DASHSCOPE_API_KEY"})
        await client_ws.close()
        return
    if ws_connect is None:
        await client_ws.send_json({"type": "error", "message": "服务端缺少 websockets 库"})
        await client_ws.close()
        return

    headers = [("Authorization", f"Bearer {api_key}")]
    instructions = BASE_INSTRUCTIONS
    if can_persist and user:
        ctx = _build_context_brief(user["user_id"], mid)
        if ctx:
            instructions += ctx

    turns: list = []
    cur_user = {"text": ""}
    cur_ai = {"text": ""}

    try:
        async with ws_connect(
            UPSTREAM_URL,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=30,
        ) as upstream:
            session_cfg = {
                "event_id": "evt_nn_session_init",
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "voice": "Serena",
                    "instructions": instructions,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_sample_rate": 16000,
                    "output_audio_sample_rate": 24000,
                    "input_audio_transcription": {"model": "paraformer-realtime-v2"},
                    # 保留服务端 VAD，但把"算作说完"的静音从默认 600ms 拉长到 7000ms
                    # 这样人正常的停顿/换气不会被切断；客户端"我说完了"关键词作为快捷提交
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 7000,
                    },
                },
            }
            await upstream.send(json.dumps(session_cfg, ensure_ascii=False))

            async def client_to_upstream():
                try:
                    while True:
                        msg = await client_ws.receive_text()
                        await upstream.send(msg)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    api_logger.debug("[realtime] c2u closed: %s", e)

            async def upstream_to_client():
                try:
                    async for raw in upstream:
                        if isinstance(raw, (bytes, bytearray, memoryview)):
                            raw = bytes(raw).decode("utf-8", errors="ignore")
                        text = str(raw)
                        try:
                            evt = json.loads(text)
                            t = evt.get("type", "")
                            if t == "conversation.item.input_audio_transcription.completed":
                                u = (evt.get("transcript") or "").strip()
                                if u:
                                    cur_user["text"] = u
                            elif t == "response.audio_transcript.delta":
                                d = evt.get("delta") or ""
                                cur_ai["text"] += d
                            elif t == "response.audio_transcript.done":
                                ai = cur_ai["text"].strip()
                                if cur_user["text"] or ai:
                                    if cur_user["text"]:
                                        turns.append({"role": "user", "content": cur_user["text"]})
                                    if ai:
                                        turns.append({"role": "assistant", "content": ai})
                                cur_user["text"] = ""
                                cur_ai["text"] = ""
                        except Exception:
                            pass
                        await client_ws.send_text(text)
                except Exception as e:
                    api_logger.debug("[realtime] u2c closed: %s", e)

            tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception as e:
        try:
            await client_ws.send_json({"type": "error", "message": f"上游连接失败: {e}"})
        except Exception:
            pass

    try:
        await client_ws.close()
    except Exception:
        pass

    if can_persist and turns and user:
        try:
            _persist_realtime_turns(user["user_id"], mid, turns)
        except Exception as e:
            print("[realtime] persist failed:", e)
