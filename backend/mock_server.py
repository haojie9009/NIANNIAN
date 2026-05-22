"""
念念 Mock API Server — 本地模拟 LLM / 302.ai / 可灵，零 API 消费。

用法：
  1. 启动：python backend/mock_server.py
  2. .env 里设置 NIAN_LLM_BASE_URL=http://localhost:8099
  3. 正常启动后端，所有 LLM 调用走本地 mock

特性：
  - 模拟 OpenAI 兼容接口（/v1/chat/completions）
  - 模拟 302.ai 图生视频提交 + 轮询
  - 模拟可灵官方视频提交 + 轮询
  - 模拟图片生成（返回 1x1 像素 PNG 的 base64）
  - 可配置延迟、失败场景
"""
import base64
import io
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ── 1x1 透明 PNG base64（用于 mock 图片生成）────────────────────────────────
_MOCK_PNG_B64 = base64.b64encode(
    bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG header
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # 1x1
        0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,  # IDAT
        0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
        0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,  # IEND
        0x42, 0x60, 0x82,
    ])
).decode()

# ── 预设响应模板 ─────────────────────────────────────────────────────────────

_MV01_RESPONSE = {
    "deceased_name": "陈文斌",
    "deceased_gender": "男",
    "birth_date": "1948年10月15日",
    "death_date": "2025年4月8日",
    "occupation": "退休工程师",
    "ceremony_date": "2025年4月15日",
    "ceremony_venue": "上海市黄浦区殡仪馆思源厅",
    "total_duration_sec": 300,
    "speaker_name": "陈明",
    "speaker_relation": "儿子",
    "speaker_style": "深情克制，儒雅温暖",
    "style_preference": "warm_nostalgia",
    "family_memory_text": "父亲是一个话不多但做什么都认真的人。",
    "last_wishes": "希望家人身体健康。",
}

_MV02_RESPONSE = {
    "validation_passed": True,
    "confirmed_fields": ["deceased_name", "birth_date", "death_date"],
    "missing_fields": [],
}

_MV03_RESPONSE = {
    "tone": "温暖怀旧",
    "visual_style": "柔和暖色调，胶片质感",
    "character_bible": {
        "display_name": "陈文斌",
        "character_id": "chen_wenbin",
        "character_dna": {
            "facial_features": "国字脸，戴黑框眼镜，眼神温和坚定",
            "body_features": "中等身材，略微驼背",
            "clothing_style": "蓝色中山装，整洁朴素",
            "mannerisms": "说话前习惯推一下眼镜",
        },
    },
    "supporting_cast": [
        {"name": "李秀英", "role_label": "妻子", "description": "温柔贤惠，相伴五十年"},
    ],
}

_MV04_RESPONSE = {
    "scenes": [
        {
            "scene_id": "S1",
            "scene_ref": "intro",
            "description": "老照片风格的开场，陈文斌年轻时的工作台",
            "time": "0:00-0:10",
            "mj_prompt": "old workshop desk, vintage photograph style, warm golden light",
        },
        {
            "scene_id": "S2",
            "scene_ref": "memory",
            "description": "公园里打太极拳的清晨",
            "time": "0:00-0:08",
            "mj_prompt": "morning park, tai chi, peaceful elderly man, soft morning light",
        },
        {
            "scene_id": "S3",
            "scene_ref": "family",
            "description": "孙女高考前父亲默默陪伴的温馨夜晚",
            "time": "0:00-0:12",
            "mj_prompt": "warm study room at night, father quietly placing food beside desk",
        },
    ]
}

_MV05_RESPONSE = {"avatar_rendered": True, "quality": "high"}
_MV06_RESPONSE = {"final_cut_url": "/api/outputs/final_cuts/mock_cut.mp4"}

_MOCK_RESPONSES = {
    "MV01": _MV01_RESPONSE,
    "MV02": _MV02_RESPONSE,
    "MV03": _MV03_RESPONSE,
    "MV04": _MV04_RESPONSE,
    "MV05": _MV05_RESPONSE,
    "MV06": _MV06_RESPONSE,
}

# 通用对话 mock 回复
_CHAT_REPLIES = [
    "我理解你的感受，这些回忆真的很珍贵。",
    "谢谢你分享这些，让我更了解 TA 了。",
    "听起来 TA 是一个温暖又坚强的人。",
    "这些细节很动人，我们会把它们融入影像里。",
]

# ── 视频任务模拟存储 ──────────────────────────────────────────────────────
_mock_video_tasks: Dict[str, Dict[str, Any]] = {}

# ── 启动计时（模拟视频生成需要一定时间）────────────────────────────────────
_task_start_times: Dict[str, float] = {}
_MOCK_VIDEO_READY_AFTER = 5  # 秒后"完成"


# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="NIANNIAN Mock LLM Server")


def _extract_skill_name(messages: List[Dict]) -> Optional[str]:
    """从 system prompt 或 user content 中猜测是哪个 MV 技能"""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            for mv in ["MV01", "MV02", "MV03", "MV04", "MV05", "MV06"]:
                if mv in content:
                    return mv
            # 检查是否是 WECHAT01
            if "WECHAT01" in content or "聊天" in content[:100]:
                return "WECHAT01"
    return None


def _try_parse_json_payload(user_content: str) -> Optional[Dict]:
    """尝试从 user content 中解析 JSON"""
    try:
        return json.loads(user_content)
    except Exception:
        return None


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 兼容接口 — 所有文本/结构化请求"""
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", "mock-model")
    temperature = body.get("temperature", 0.5)

    # 识别是哪个技能
    skill_name = _extract_skill_name(messages)

    if skill_name and skill_name in _MOCK_RESPONSES:
        # MV 技能 → 返回预设 JSON
        response_content = json.dumps(_MOCK_RESPONSES[skill_name], ensure_ascii=False)
    elif skill_name == "WECHAT01":
        response_content = json.dumps({
            "tone": "温和朴实",
            "speech_patterns": ["嗯", "好嘞", "没事儿"],
            "typical_topics": ["家常", "工作"],
            "humor_level": 3,
            "response_style": "自然随和",
            "avg_sentence_length": "中等",
            "emotional_words": ["心疼", "欣慰"],
            "signature_phrases": ["活着就好"],
            "special_habits": "说话前习惯停顿一下",
        }, ensure_ascii=False)
    else:
        # 通用对话 → 返回预设文字
        reply = _CHAT_REPLIES[0]
        is_json_mode = body.get("response_format", {}).get("type") == "json_object"
        if is_json_mode:
            # 尝试从 payload 中提取期望的字段
            user_content = ""
            for msg in messages:
                if msg.get("role") == "user":
                    c = msg.get("content", "")
                    if isinstance(c, str):
                        user_content = c
                    break
            parsed = _try_parse_json_payload(user_content)
            if parsed and isinstance(parsed, dict):
                # 返回同样结构的 mock 数据
                mock = {}
                for key in parsed:
                    v = parsed[key]
                    if isinstance(v, str):
                        mock[key] = f"mock_{key}"
                    elif isinstance(v, (list, dict)):
                        mock[key] = v
                    else:
                        mock[key] = v
                response_content = json.dumps(mock, ensure_ascii=False)
            else:
                response_content = json.dumps({"reply": reply}, ensure_ascii=False)
        else:
            response_content = reply

    # 模拟延迟
    delay = body.get("_mock_delay", 0.2)
    time.sleep(delay)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions():
    """Whisper 语音转写 mock"""
    return {"text": "这是一段测试语音的转写结果。"}


@app.post("/mock/v1/images/generate")
async def generate_image(request: Request):
    """图片生成 mock — 返回 1x1 PNG base64"""
    return {"b64": _MOCK_PNG_B64}


# ── 302.ai 视频接口 mock ──────────────────────────────────────────────────

@app.post("/klingai/m2v_26_image2video_5s")
async def video_submit_302(request: Request):
    """302.ai 视频提交 mock"""
    task_id = f"kling_mock_{uuid.uuid4().hex[:12]}"
    _mock_video_tasks[task_id] = {
        "status": 5,  # 排队中
        "source": "302ai",
    }
    _task_start_times[task_id] = time.time()
    return {
        "status": 200,
        "result": 1,
        "data": {"task": {"id": task_id}},
    }


@app.get("/klingai/fetch")
async def video_fetch_302(task_id: str):
    """302.ai 视频状态查询 mock"""
    if task_id not in _mock_video_tasks:
        return {"status": 404, "message": "task not found"}

    task = _mock_video_tasks[task_id]
    elapsed = time.time() - _task_start_times.get(task_id, 0)

    if elapsed < _MOCK_VIDEO_READY_AFTER:
        task["status"] = 10  # 处理中
        return {"status": 200, "data": {"status": 10, "task_id": task_id}}
    else:
        task["status"] = 99  # 完成
        return {
            "status": 200,
            "data": {
                "status": 99,
                "task_id": task_id,
                "works": [{"resource": f"https://mock.302.ai/videos/{task_id}.mp4"}],
            },
        }


# ── 可灵官方 API mock ─────────────────────────────────────────────────────

@app.post("/v1/videos/image2video")
async def video_submit_kling(request: Request):
    """可灵官方视频提交 mock"""
    task_id = f"kling_official_{uuid.uuid4().hex[:12]}"
    _mock_video_tasks[task_id] = {
        "status": "submitted",
        "source": "kling",
    }
    _task_start_times[task_id] = time.time()
    return {
        "code": 0,
        "message": "success",
        "data": {"task_id": task_id},
    }


@app.get("/v1/videos/image2video/{task_id}")
async def video_status_kling(task_id: str):
    """可灵官方视频状态查询 mock"""
    if task_id not in _mock_video_tasks:
        return {"code": 1, "message": "task not found"}

    elapsed = time.time() - _task_start_times.get(task_id, 0)

    if elapsed < _MOCK_VIDEO_READY_AFTER:
        return {
            "code": 0,
            "data": {
                "task_status": "processing",
                "task_id": task_id,
            },
        }
    else:
        return {
            "code": 0,
            "data": {
                "task_status": "succeed",
                "task_id": task_id,
                "task_result": {
                    "videos": [{"url": f"https://mock.kling.ai/videos/{task_id}.mp4"}],
                },
            },
        }


# ── 管理接口 ──────────────────────────────────────────────────────────────

@app.get("/mock/status")
async def mock_status():
    """查看 mock server 状态和已提交任务"""
    tasks = {}
    for tid, t in _mock_video_tasks.items():
        tasks[tid] = {
            **t,
            "elapsed_sec": round(time.time() - _task_start_times.get(tid, 0), 1),
        }
    return {
        "server": "running",
        "video_tasks": tasks,
        "mock_ready_after_sec": _MOCK_VIDEO_READY_AFTER,
    }


@app.post("/mock/reset")
async def mock_reset():
    """清空所有 mock 任务状态"""
    _mock_video_tasks.clear()
    _task_start_times.clear()
    return {"ok": True}


@app.get("/mock/health")
async def health():
    return {"status": "ok", "time": time.time()}


# ── 入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  念念 Mock API Server")
    print("  监听端口: 8099")
    print("  使用方法:")
    print("  1. .env 中设置 NIAN_LLM_BASE_URL=http://localhost:8099")
    print("  2. 正常启动后端服务")
    print("  3. 所有 LLM 调用将使用 mock 响应")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8099)
