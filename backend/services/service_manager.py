# backend/services/service_manager.py
# 统一出口 —— routers 只允许从这里 import。
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# 按 sid 加锁，防止同一 session 的 pipeline_chain 并发执行
_pipeline_chain_locks: Dict[str, threading.Lock] = {}
_pipeline_chain_locks_lock = threading.Lock()


def _get_chain_lock(sid: str) -> threading.Lock:
    with _pipeline_chain_locks_lock:
        if sid not in _pipeline_chain_locks:
            _pipeline_chain_locks[sid] = threading.Lock()
        return _pipeline_chain_locks[sid]

from . import gate_manager, session_store  # noqa: F401

from .llm_client import (
    _BGM_STYLE_MAP,
    PRIMARY_CLIENT,
    TEXT_MODEL,
    TEXT_FALLBACK_MODEL,
    DIALOGUE_MODEL,
    _CACHE_MODE,
    call_skill,
    call_memorial_chat,
    call_freeform,
    call_structured,
    describe_image,
    transcribe_audio,
    seed_tts,
    generate_bgm_suno,
    build_scene_prompts,
    generate_image_302,
    generate_video_302ai_i2v,
    generate_video_kling,
)
from .skill_loader import load_skill
from logger import svc_logger

ROOT_DIR    = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR  = ROOT_DIR / "skills"
ASSET_DIR   = ROOT_DIR / "asset"
OUTPUTS_DIR = ROOT_DIR / "backend" / "outputs"
UPLOADS_DIR = OUTPUTS_DIR / "uploads"
FINAL_DIR   = OUTPUTS_DIR / "final_cuts"
GENERATED_DIR = OUTPUTS_DIR / "generated"
GEN_IMAGES_DIR = GENERATED_DIR / "images"
GEN_VIDEOS_DIR = GENERATED_DIR / "videos"
AUDIO_OUTPUT_DIR = GENERATED_DIR / "audio"

for _d in (OUTPUTS_DIR, UPLOADS_DIR, FINAL_DIR, GENERATED_DIR, GEN_IMAGES_DIR, GEN_VIDEOS_DIR, AUDIO_OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ── 测试数据 ───────────────────────────────────────────────────────────────
TEST_DATA: Dict[str, Any] = {
    "deceased_name": "陈文斌",
    "deceased_gender": "男",
    "birth_date": "1948年10月15日",
    "death_date": "2025年4月8日",
    "occupation": "退休工程师（原上海机床厂车间主任、某机械制造公司技术部经理）",
    "ceremony_date": "2025年4月15日",
    "ceremony_venue": "上海市黄浦区殡仪馆思源厅",
    "total_duration_sec": 300,
    "speaker_name": "陈明",
    "speaker_relation": "儿子",
    "speaker_style": "深情克制，儒雅温暖，感恩回望，不过度煽情",
    "style_preference": "warm_nostalgia",
    "family_memory_text": (
        "父亲是一个话不多但做什么都认真到底的人。青年时戴黑框眼镜，穿蓝色中山装，"
        "眼神里总有一种让人安心的笃定。退休后每天清晨和母亲去公园打太极拳，风雨无阻，"
        "说「动起来才有精气神」。他爱好书法多年，书法作品多次在社区展览中获奖；"
        "还坚持集邮，把每一枚邮票都仔细收进册子，说「小小方寸，装着大世界」。"
        "2020年起成为社区志愿者，帮邻里修电器、疏通水管、调解纠纷，从不推辞，"
        "说「退休了更要做点有用的事」。\n\n"
        "事迹一：1968年响应上山下乡号召赴安徽阜阳插队，十年知青岁月中学会种地、木工、电工，"
        "1978年高考恢复以优异成绩考上上海工业大学机械工程系，是全公社唯一考上大学的知青。\n"
        "事迹二：1990年担任上海机床厂车间主任，带领团队攻克多项技术难关，"
        "1985年起连续多年被评为厂级先进工作者，同事们都叫他「陈工」。\n"
        "事迹三：1975年在安徽阜阳与母亲李秀英举办简朴婚礼，相伴五十年从未分离，"
        "2025年迎来金婚纪念。\n"
        "事迹四：孙女陈雨桐高考前，父亲每天为她备好夜宵放在书桌旁，从不打扰，"
        "只在门缝里静静看一眼，说「孩子努力，我们陪着就够了」。"
    ),
    "last_wishes": "希望家人身体健康、和和睦睦，盼孙女陈雨桐学业顺遂。",
}


# ── Pipeline 步骤映射（与根目录 pipeline_runner.py 对齐）────────────────────
MV_FILES = {
    "MV01": "MV01-interview.md",
    "MV02": "MV02-validation.md",
    "MV03": "MV04-bible-lock.md",
    "MV04": "MV03-storyboard.md",
    "MV05": "MV05-avatar-render.md",
    "MV06": "MV06-final-cut.md",
}


def run_pipeline_step(sid: str, mv_id: str) -> Dict[str, Any]:
    """运行单个 pipeline 步骤（轻量版，供 FastAPI 调用）"""
    import time as _t
    s = session_store.require(sid)
    gate = s["gate"]

    if mv_id not in MV_FILES:
        return {"error": True, "message": f"unknown step: {mv_id}"}

    if not gate_manager.can_run(gate, mv_id):
        return {"error": True, "message": f"gate not open: {mv_id} 需要前置步骤完成"}

    gate_manager.set_running(gate, mv_id)
    s["pipeline_state"][mv_id] = {"status": "running", "duration_sec": None, "error": None}
    t0 = _t.time()

    try:
        skill_path = SKILLS_DIR / MV_FILES[mv_id]
        system_prompt = load_skill(str(skill_path))

        # payload：form_data（过滤空字符串）+ 已有 mv 输出
        cleaned_form = {k: v for k, v in s["form_data"].items() if v != ""}
        payload: Dict[str, Any] = {
            "form_data": cleaned_form,
            "mv_outputs": s["mv_outputs"],
        }
        result = call_skill(mv_id, system_prompt, payload)
        elapsed = round(_t.time() - t0, 2)

        if isinstance(result, dict) and result.get("error"):
            gate_manager.reject(gate, mv_id, {})
            s["pipeline_state"][mv_id] = {
                "status": "error", "duration_sec": elapsed, "error": result.get("message", "unknown")
            }
            session_store.update(sid)  # 写盘持久化
            return {"error": True, "step": mv_id, "message": result.get("message")}

        s["mv_outputs"][mv_id] = result
        gate_manager.approve(gate, mv_id)
        s["pipeline_state"][mv_id] = {"status": "approved", "duration_sec": elapsed, "error": None}
        session_store.update(sid)  # 写盘持久化

        # 持久化到 outputs/
        try:
            import json as _json
            out_path = OUTPUTS_DIR / f"{sid}_{mv_id.lower()}.json"
            out_path.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return {"ok": True, "step": mv_id, "duration_sec": elapsed, "result": result}

    except Exception as exc:
        elapsed = round(_t.time() - t0, 2)
        gate_manager.reject(gate, mv_id, {})
        s["pipeline_state"][mv_id] = {"status": "error", "duration_sec": elapsed, "error": str(exc)}
        session_store.update(sid)  # 写盘持久化
        return {"error": True, "step": mv_id, "message": str(exc)}


# ── 深度搜索（302.ai perplexity/sonar-pro）────────────────────────────────
_SEARCH_MODELS = ["perplexity/sonar-pro", "perplexity/sonar", "gpt-4o-search-preview"]

_DEEP_SEARCH_SYSTEM = """你是念念追思影像制作助手，具备联网实时搜索能力。
用户想了解某位人物的生平资料，以便制作追思影像。请联网搜索后，用温暖自然的中文整理：

### 一、基本信息
全名、生卒年月（如有）、主要职业/身份、籍贯。

### 二、人生经历亮点
按时间顺序列出 3-6 个重要节点。

### 三、性格与精神遗产
性格、价值观、主要贡献（100字以内）。

### 四、适合追思影像的素材线索
2-4 个最具画面感的场景、故事或情感记忆点。

若无公开资料请如实告知。输出语气温暖，不要使用"根据搜索结果"等机械表述。"""

_FILL_SYSTEM = """根据已整理资料，提取以下字段，严格输出 JSON，不加任何解释：
{
  "deceased_name": "姓名",
  "deceased_gender": "男 或 女 或 不便告知",
  "birth_date": "XXXX年X月X日 或 空",
  "death_date": "XXXX年X月X日 或 空",
  "occupation": "主要职业",
  "family_memory_text": "家属视角的温暖回忆叙述，200-350字"
}
信息不足的字段填空字符串。"""


def deep_search(query: str, extra: str = "") -> Dict[str, Any]:
    """调用 302.ai 联网搜索，返回 (organized_text, used_model)。失败降级到知识库模式。"""
    user_msg = f"请帮我搜索并整理：{query}"
    if extra.strip():
        user_msg += f"\n\n补充背景：{extra.strip()}"

    for model in _SEARCH_MODELS:
        try:
            resp = PRIMARY_CLIENT.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _DEEP_SEARCH_SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.4,
                max_tokens=1400,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                return {"organized": content, "model": model, "fallback": False}
        except Exception:
            continue

    # 降级
    kb_prefix = (
        "【提示：联网搜索模型暂时不可用，以下内容来自 AI 知识库，"
        "可能存在知识截止日期限制，建议核实后再填写。】\n\n"
    )
    kb_system = _DEEP_SEARCH_SYSTEM.replace("具备联网实时搜索能力。", "请根据已有知识回答。")
    for m in [TEXT_MODEL, TEXT_FALLBACK_MODEL]:
        try:
            resp = PRIMARY_CLIENT.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": kb_system},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.5,
                max_tokens=1200,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                return {"organized": kb_prefix + content, "model": f"{m}（知识库模式）", "fallback": True}
        except Exception:
            continue

    return {"organized": "抱歉，AI 服务暂时不可用，请稍后重试。", "model": "(失败)", "fallback": True}


def deep_search_extract_fields(organized: str, query: str) -> Dict[str, Any]:
    """从整理文本中提取可填表单字段"""
    import json as _json, re as _re
    for m in [TEXT_MODEL, TEXT_FALLBACK_MODEL]:
        try:
            resp = PRIMARY_CLIENT.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": _FILL_SYSTEM},
                    {"role": "user",   "content": f"搜索词：{query}\n\n整理资料：\n{organized}"},
                ],
                temperature=0.2,
                max_tokens=700,
            )
            raw = resp.choices[0].message.content or "{}"
            mt = _re.search(r"\{.*\}", raw, _re.S)
            if mt:
                return _json.loads(mt.group())
        except Exception:
            continue
    return {}


# ── 念念 AI 对话（intake step3）────────────────────────────────────────────
_MEMORIAL_SYSTEM = (
    "你是「念念 AI」，一位温柔体贴的追思影像制作助手，帮助家属把对亲人的记忆整理成珍贵的追思影像。"
    "说话像温暖的长者朋友，用口语化自然流畅的中文，语气轻柔有耐心。"
    "每次回复 120-200 字，用自然段落，可用换行分段。"
    "第一次回复：先温暖开场感谢家属分享，然后自然总结已了解的信息（约 40 字，不要用字段名称），"
    "温柔指出 1-2 个可以补充的地方，用一句鼓励的话结尾。"
    "后续回复：先肯定补充的信息，信息充分时主动说可以开始制作了。"
    "绝对不要输出 JSON、技术参数、星号格式。"
)


def memorial_greeting(form_data: Dict[str, Any]) -> str:
    """生成念念 AI 开场白"""
    summary = _form_summary_for_ai(form_data)
    msgs = [{"role": "user", "content": f"以下是家属填写的信息，请你温暖开场：\n{summary}"}]
    return call_memorial_chat(_MEMORIAL_SYSTEM, msgs)


def memorial_reply(form_data: Dict[str, Any], history: List[Dict[str, str]]) -> str:
    """念念 AI 多轮回复"""
    summary = _form_summary_for_ai(form_data)
    seeded = [{"role": "user", "content": f"家属背景信息：\n{summary}"}] + history
    return call_memorial_chat(_MEMORIAL_SYSTEM, seeded)


# ── 数字人对话（独立于追思影像建档流程）────────────────────────────────────
# 用户上传聊天记录 → 风格分析 → 人设融合 → 与"逝者"对话
import csv as _csv
import io as _io
import json as _json2
import re as _re2


def parse_chat_file(file_bytes: bytes, filename: str, target: str) -> List[Dict[str, Any]]:
    """解析微信聊天记录（CSV/JSON/TXT）→ 消息列表"""
    messages: List[Dict[str, Any]] = []
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    target = (target or "").strip()

    if ext == "csv":
        text = file_bytes.decode("utf-8-sig", errors="replace")
        reader = _csv.DictReader(_io.StringIO(text))
        for row in reader:
            sender    = row.get("StrTalker") or row.get("sender") or row.get("NickName", "")
            is_sender = str(row.get("IsSender", "0"))
            msg_type  = str(row.get("Type", "1"))
            content   = row.get("StrContent") or row.get("content", "")
            if is_sender == "0" and msg_type == "1" and (not target or target in sender) and content.strip():
                messages.append({"sender": sender, "content": content.strip()})
    elif ext == "json":
        try:
            data = _json2.loads(file_bytes.decode("utf-8", errors="replace"))
        except Exception:
            data = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (data.get("messages") or data.get("msg") or
                     data.get("records") or data.get("data") or [])
            if not items:
                for v in data.values():
                    if isinstance(v, list) and v:
                        items = v
                        break
        else:
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sender  = (item.get("sender") or item.get("from") or item.get("NickName")
                       or item.get("talker") or item.get("StrTalker") or "")
            content = (item.get("content") or item.get("text") or item.get("StrContent")
                       or item.get("msg") or "")
            is_sender = str(item.get("IsSender", item.get("isSender", "")))
            msg_type  = str(item.get("Type", item.get("type", "1")))
            if is_sender == "1":
                continue
            if is_sender == "0" and msg_type not in ("1", ""):
                continue
            content = str(content).strip()
            if not content:
                continue
            if not target or target in sender:
                messages.append({"sender": sender, "content": content})
    elif ext == "txt":
        text = file_bytes.decode("utf-8", errors="replace")
        pattern = _re2.compile(
            r'(?:\[([^\]]+)\]\s+)?([^\n:：\(]+)(?:\([^)]*\))?[：:]\s*([^\n]+(?:\n(?!\[|\d{4})[^\n]+)*)',
            _re2.MULTILINE,
        )
        for m in pattern.finditer(text):
            sender  = m.group(2).strip()
            content = m.group(3).strip()
            if (not target or target in sender) and content:
                messages.append({"sender": sender, "content": content})
    return messages


def analyze_chat_style(messages: List[Dict[str, Any]], target_name: str, role_desc: str = "") -> Dict[str, Any]:
    """调用 WECHAT01 skill 分析风格"""
    skill_path = SKILLS_DIR / "WECHAT01-style-analysis.md"
    if not skill_path.exists():
        return {"error": True, "message": "WECHAT01 skill 文件缺失"}
    sample = messages[-300:] if len(messages) > 300 else messages
    sample_text = "\n".join(m["content"] for m in sample)
    payload: Dict[str, Any] = {
        "target_name": target_name or "目标人物",
        "messages": sample_text,
        "message_count": len(messages),
    }
    if role_desc.strip():
        payload["role_description"] = role_desc.strip()
    prompt = load_skill(str(skill_path))
    return call_skill("WECHAT01", prompt, payload)


def merge_persona(dna: Dict[str, Any], current_override: str, new_input: str) -> str:
    """智能融合人设描述（沿用 Streamlit 端实现）"""
    dna_summary = (
        f"语气基调: {dna.get('tone', '')}, "
        f"常用口头禅: {'、'.join(dna.get('speech_patterns', [])[:5])}, "
        f"常聊话题: {'、'.join(dna.get('typical_topics', [])[:3])}, "
        f"幽默程度: {dna.get('humor_level', 3)}/5, "
        f"回应风格: {dna.get('response_style', '')}"
    )
    merge_prompt = (
        f"你是一个角色人设管理助手。用户正在为数字人动态调整人设描述。\n\n"
        f"【已有角色 DNA 摘要】\n{dna_summary}\n\n"
        f"【当前角色背景描述】\n{current_override if current_override.strip() else '（暂无）'}\n\n"
        f"【用户新增/修改内容】\n{new_input}\n\n"
        f"请智能融合：保留有用旧设定，以新内容优先，去除矛盾重复，"
        f"用自然流畅中文描述，不超过250字。"
        f"直接返回融合后文本，不要任何解释或前缀。"
    )
    try:
        result = call_memorial_chat(
            system_prompt="你是角色人设管理助手，只返回融合结果文本，不解释。",
            messages=[{"role": "user", "content": merge_prompt}],
            model=DIALOGUE_MODEL,
        )
        return (result or "").strip()
    except Exception:
        return (current_override + "\n\n[新增] " + new_input).strip()


def build_persona_system_prompt(dna: Dict[str, Any], name: str, extra_desc: str = "") -> str:
    speech_patterns = "、".join(dna.get("speech_patterns", [])[:10])
    signatures = "\n".join(f"  - {p}" for p in dna.get("signature_phrases", []))
    topics    = "、".join(dna.get("typical_topics", [])[:5])
    emo_words = "、".join(dna.get("emotional_words", [])[:8])
    habits    = dna.get("special_habits", "")
    extra_section = f"\n【角色背景补充】\n{extra_desc}\n" if extra_desc.strip() else ""
    return (
        f"你现在扮演一个真实存在过的人：{name}。\n\n"
        f"你的任务是完全以 {name} 的口吻和风格与用户对话，让用户感受到 TA 仍然在陪伴着自己。"
        f"{extra_section}\n"
        f"【{name} 的语言风格特征】\n"
        f"- 常用词/口头禅：{speech_patterns}\n"
        f"- 句子长度风格：{dna.get('avg_sentence_length', '中等')}\n"
        f"- 情感基调：{dna.get('tone', '温和')}\n"
        f"- 幽默程度：{dna.get('humor_level', 3)}/5\n"
        f"- 常聊话题：{topics}\n"
        f"- 常用情感词：{emo_words}\n"
        f"- 特殊语言习惯：{habits}\n"
        f"- 标志性句式：\n{signatures}\n"
        f"- 回应风格：{dna.get('response_style', '自然随和')}\n\n"
        f"【角色扮演守则】\n"
        f"1. 始终保持角色，不要说「我是AI」或打破扮演\n"
        f"2. 回复长度符合 TA 原本的风格\n"
        f"3. 适当使用口头禅和标志性句式，但不要每句都重复\n"
        f"4. 语气温暖真实，像真正的对话而不是朗诵\n"
        f"5. 如果用户问到不知道的事，以 {name} 的性格自然回应\n"
        f"6. 每次回复后可自然地反问或延续话题\n"
        f"7. 不使用 Markdown 格式符号，保持口语化\n"
        f"8. 每次回复控制在1-4句话，除非用户要求详细"
    )


def dialogue_reply(dna: Dict[str, Any], name: str, override: str, history: List[Dict[str, str]]) -> str:
    """数字人对话回复"""
    sys_prompt = build_persona_system_prompt(dna, name or "TA", override or "")
    return call_memorial_chat(
        system_prompt=sys_prompt,
        messages=history[-20:],
        model=DIALOGUE_MODEL,
    )


def _form_summary_for_ai(form_data: Dict[str, Any]) -> str:
    name = form_data.get("deceased_name", "")
    rel  = form_data.get("speaker_relation", "")
    birth = form_data.get("birth_date", "")
    death = form_data.get("death_date", "")
    occ  = form_data.get("occupation", "")
    mem  = form_data.get("family_memory_text", "")
    wish = form_data.get("last_wishes", "")
    parts = []
    if name: parts.append(f"亲人姓名：{name}")
    if rel:  parts.append(f"发言人是逝者的{rel}")
    if birth:parts.append(f"出生：{birth}")
    if death:parts.append(f"逝世：{death}")
    elif birth: parts.append("目前在世")
    if occ:  parts.append(f"职业/身份：{occ}")
    if mem:  parts.append(f"家庭回忆：{mem[:300]}")
    if wish: parts.append(f"心愿/寄语：{wish[:150]}")
    return "\n".join(parts) if parts else "（家属尚未填写详细信息）"


# ── 影像预告（preview）：用大白话讲解流程 ──────────────────────────────────
_PREVIEW_SYS = (
    "你是一位亲切的追思影像讲解员，帮助家属提前了解即将制作的影片内容。"
    "请根据下面提供的逝者信息，用最通俗的大白话（就像面对面和家里老人讲话一样），"
    "把这部追思影像的大致流程讲清楚：先是什么，然后是什么，最后是什么。"
    "语气温柔、耐心，像邻居奶奶聊天一样自然。\n"
    "格式要求：\n"
    "- 用三段结构，每段 2-4 句话\n"
    "- 不要用专业词汇，不要说'分镜'、'AI生成'、'模型'这类词\n"
    "- 每段开头加上序号表情：①②③\n"
    "- 总长度控制在 150-220 字"
)


def memorial_preview(form_data: Dict[str, Any], mv01_result: Optional[Dict[str, Any]] = None) -> str:
    """根据表单 + MV01 输出生成大白话流程讲解"""
    import json as _json
    if mv01_result:
        info = _json.dumps(mv01_result, ensure_ascii=False, indent=2)
    else:
        info = _form_summary_for_ai(form_data)
    prompt = f"以下是逝者和家属的信息：\n\n{info}\n\n请用大白话帮家属讲讲这部影片的流程。"
    try:
        return call_memorial_chat(_PREVIEW_SYS, [{"role": "user", "content": prompt}])
    except Exception as e:
        return f"① 我们会先用您填写的内容整理出一份完整的故事大纲。\n② 接着会确定影像的整体氛围、主角的样子，让画面更贴近 TA。\n③ 最后会一帧一帧把回忆做成可以播放的影片。\n\n（系统提示：预览生成遇到问题：{e}）"


# ── MV 步骤后大白话总结 ───────────────────────────────────────────────────
_MV01_SUMMARY_SYS = (
    "你是念念追思影像制作助手，帮家属用最温柔口语化的中文描述影像制作进展。"
    "收到 JSON 数据后，用 80-120 字的自然语言告诉家属：我们了解了哪些信息，"
    "影像会呈现什么样的感觉。不要出现任何技术词汇、字段名、JSON。语气温暖贴心。"
    "只输出一段话，不要分点、不要标题。"
)
_MV03_SUMMARY_SYS = (
    "你是念念追思影像制作助手。根据影像三要素 JSON，用最温柔自然的中文，"
    "用 80-120 字告诉家属：我们为这部影像确定了什么样的基调、主角形象和画面氛围。"
    "不要出现任何 JSON、字段名或技术词汇。语气温暖，像在讲述一个美好的计划。"
    "必须使用 JSON 中真实的人物姓名，绝对不得使用任何无关的示例名称。只输出一段话，不要分点。"
)


def _safe_mv_summary(sys_prompt: str, payload: Dict[str, Any], fallback: str) -> str:
    import json as _json
    try:
        return call_freeform(sys_prompt, _json.dumps(payload, ensure_ascii=False))
    except Exception:
        return fallback


def run_pipeline_chain(sid: str) -> Dict[str, Any]:
    """串行执行 MV01 → MV02 → MV03，并附带两段大白话总结气泡。
    复刻 archive/streamlit/pages/pipeline.py 的 run_pipeline() 逻辑。"""
    s = session_store.require(sid)
    bubbles: List[Dict[str, str]] = []   # [{role:'ai', content}]
    errors: List[Dict[str, str]] = []

    # ── MV01（若已运行就直接读取）───────────────────────────────
    mv01_out = s["mv_outputs"].get("MV01")
    if not mv01_out:
        r = run_pipeline_step(sid, "MV01")
        if r.get("error"):
            errors.append({"step": "MV01", "message": r.get("message", "未知错误")})
            return {"ok": False, "bubbles": bubbles, "errors": errors,
                    "scenes": [], "mv03": {}}
        mv01_out = r["result"]

    # 气泡①：MV01 摘要
    bubbles.append({
        "role": "ai",
        "content": _safe_mv_summary(_MV01_SUMMARY_SYS, mv01_out,
                                    "我们已经把您讲述的内容整理好了，影像将围绕这些珍贵的记忆展开。"),
    })

    # ── MV02 静默运行 ────────────────────────────────────────
    if not s["mv_outputs"].get("MV02"):
        run_pipeline_step(sid, "MV02")

    # ── MV03 三要素锁定 ──────────────────────────────────────
    mv03_out = s["mv_outputs"].get("MV03")
    if not mv03_out:
        r = run_pipeline_step(sid, "MV03")
        if r.get("error"):
            errors.append({"step": "MV03", "message": r.get("message", "未知错误")})
            return {"ok": False, "bubbles": bubbles, "errors": errors,
                    "scenes": [], "mv03": {}}
        mv03_out = r["result"]

    # 气泡②：MV03 三要素总结（注入真实姓名防止 LLM 误用）
    summary_payload = dict(mv03_out) if isinstance(mv03_out, dict) else {"raw": mv03_out}
    summary_payload["_current_deceased_name"] = s["form_data"].get("deceased_name", "")
    bubbles.append({
        "role": "ai",
        "content": _safe_mv_summary(_MV03_SUMMARY_SYS, summary_payload,
                                    "影像的基调、主角形象和画面氛围都已确定，接下来就可以进入分镜制作。"),
    })

    # 收集分镜（若 MV03 输出包含）
    scenes: List[Dict[str, Any]] = []
    if isinstance(mv03_out, dict):
        sc = mv03_out.get("scenes")
        if isinstance(sc, list):
            scenes = [x for x in sc if isinstance(x, dict)]
        elif isinstance(sc, dict):
            scenes = [sc[k] for k in sorted(sc.keys()) if isinstance(sc[k], dict)]

    return {
        "ok": True,
        "bubbles":  bubbles,
        "errors":   errors,
        "scenes":   scenes,
        "mv03":     mv03_out,
    }


def _run_pipeline_chain_work(sid: str) -> None:
    """异步后台执行 MV01→MV02→MV03，通过 pipeline_state 报告进度。"""
    import threading
    threading.Thread(target=_pipeline_chain_inner, args=(sid,), daemon=True).start()


def _pipeline_chain_inner(sid: str) -> None:
    if not _get_chain_lock(sid).acquire(blocking=False):
        return  # 该 sid 已有线程在运行，跳过
    import time as _time
    import traceback as _traceback
    import json as _json

    t_start = _time.time()
    timings: Dict[str, float] = {}

    def _set_progress(step: str, label: str, error: str | None = None) -> None:
        s = session_store.require(sid)
        s["pipeline_state"]["pipeline_chain"] = {
            "status": "error" if error else "running",
            "step": step,
            "label": label,
            "duration_sec": round(_time.time() - t_start, 1),
            "timings": dict(timings),
            "error": error,
        }
        session_store.update(sid)

    try:
        s = session_store.require(sid)
        bubbles: List[Dict[str, str]] = []

        # 检测 form_data 是否变更：与上次 MV01 运行时的摘要对比
        import hashlib as _hashlib
        current_hash = _hashlib.md5(
            _json.dumps(s["form_data"], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        stored_hash = s.get("_pipeline_chain_form_hash")
        if stored_hash and stored_hash != current_hash:
            # 表单数据已变更，清除旧输出并重新生成
            for mv in ("MV01", "MV02", "MV03"):
                s["mv_outputs"].pop(mv, None)
            s.pop("pipeline_bubbles", None)
            session_store.update(sid)

        # ── MV01 ─────────────────────────────────────────────
        mv01_out = s["mv_outputs"].get("MV01")
        if not mv01_out:
            _set_progress("mv01_running", "念念正在整理访谈内容...")
            t0 = _time.time()
            r = run_pipeline_step(sid, "MV01")
            timings["mv01"] = round(_time.time() - t0, 1)
            if r.get("error"):
                _set_progress("mv01_error", "访谈整理失败", r.get("message", "未知错误"))
                return
            s = session_store.require(sid)
            mv01_out = s["mv_outputs"].get("MV01")
            # 记录 form_data 摘要，用于后续检测数据是否变更
            s["_pipeline_chain_form_hash"] = current_hash
            session_store.update(sid)

        bubbles.append({
            "role": "ai",
            "content": _safe_mv_summary(_MV01_SUMMARY_SYS, mv01_out,
                                        "我们已经把您讲述的内容整理好了，影像将围绕这些珍贵的记忆展开。"),
        })
        _set_progress("mv01_done", "访谈整理完成")

        # ── MV02 ─────────────────────────────────────────────
        if not s["mv_outputs"].get("MV02"):
            _set_progress("mv02_running", "AI 核对中...")
            t0 = _time.time()
            run_pipeline_step(sid, "MV02")
            timings["mv02"] = round(_time.time() - t0, 1)
        _set_progress("mv02_done", "AI 核对完成")

        # ── MV03 ─────────────────────────────────────────────
        mv03_out = s["mv_outputs"].get("MV03")
        if not mv03_out:
            _set_progress("mv03_running", "方案确认中...")
            t0 = _time.time()
            r = run_pipeline_step(sid, "MV03")
            timings["mv03"] = round(_time.time() - t0, 1)
            if r.get("error"):
                _set_progress("mv03_error", "方案确认失败", r.get("message", "未知错误"))
                return
            s = session_store.require(sid)
            mv03_out = s["mv_outputs"].get("MV03")

        summary_payload = dict(mv03_out) if isinstance(mv03_out, dict) else {"raw": mv03_out}
        summary_payload["_current_deceased_name"] = s["form_data"].get("deceased_name", "")
        bubbles.append({
            "role": "ai",
            "content": _safe_mv_summary(_MV03_SUMMARY_SYS, summary_payload,
                                        "影像的基调、主角形象和画面氛围都已确定，接下来就可以进入分镜制作。"),
        })
        _set_progress("mv03_done", "方案确认完成")

        # ── 完成 ─────────────────────────────────────────────
        s = session_store.require(sid)
        s["pipeline_bubbles"] = bubbles
        # 确保 form_data 摘要已保存（MV01 缓存命中时未在上面保存）
        if not s.get("_pipeline_chain_form_hash"):
            s["_pipeline_chain_form_hash"] = current_hash
        s["pipeline_state"]["pipeline_chain"] = {
            "status": "done",
            "step": "done",
            "label": "全部完成",
            "duration_sec": round(_time.time() - t_start, 1),
            "timings": timings,
            "error": None,
        }
        session_store.update(sid)

    except Exception as exc:
        _set_progress("error", "执行出错", _traceback.format_exc())
    finally:
        _get_chain_lock(sid).release()


# ── 生成资源本地持久化 ─────────────────────────────────────────────────
def _save_generated_image(b64: str, sid: str, scene_idx: int) -> str:
    """将 base64 图片写入磁盘，返回本地 URL。同时递增 _image_version。"""
    import base64 as _b64
    filename = f"{sid}_scene{scene_idx}.png"
    path = GEN_IMAGES_DIR / filename
    path.write_bytes(_b64.b64decode(b64))
    # 递增图片版本号，用于视频缓存校验
    try:
        s = session_store.require(sid)
        mv04 = s["mv_outputs"].get("MV04")
        scenes = _get_scenes_from_mv04(mv04)
        if 0 <= scene_idx < len(scenes):
            scenes[scene_idx]["_image_version"] = scenes[scene_idx].get("_image_version", 0) + 1
        session_store.update(sid)
    except Exception:
        pass
    return f"/api/outputs/generated/images/{filename}"


def _download_generated_video(video_url: str, sid: str, scene_idx: int) -> Optional[str]:
    """下载云端视频到本地，返回本地 URL；失败返回 None。"""
    import requests as _requests_dl
    ext = "mp4"
    if "." in video_url.split("/")[-1].split("?")[0]:
        ext = video_url.split("/")[-1].split("?")[0].rsplit(".", 1)[-1].lower()
        if ext not in ("mp4", "mov", "webm"):
            ext = "mp4"
    filename = f"{sid}_scene{scene_idx}.{ext}"
    path = GEN_VIDEOS_DIR / filename
    try:
        r = _requests_dl.get(video_url, timeout=120, stream=True)
        if r.status_code == 200:
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return f"/api/outputs/generated/videos/{filename}"
        svc_logger.warning("[video_download] HTTP %d for %s", r.status_code, video_url)
    except Exception as e:
        svc_logger.exception("[video_download] failed: %s", e)
    return None


# ── 分镜场景：单镜图片/视频生成 ─────────────────────────────────────────
def _desensitize_name_in_prompt(prompt: str, form_data: Dict, bible: Optional[Dict] = None) -> str:
    """将 prompt 中的真实姓名替换为姓氏+称呼，避免可灵等平台的内容审核。

    规则：张雪峰 → 张先生（保留姓氏，去掉名字）
         张先生、张女士 → 不变
    """
    import re
    name = None
    gender = None

    # 优先从人物圣经获取
    if bible:
        name = bible.get("display_name") or bible.get("character_id")
        gender = bible.get("gender", "").lower()

    # 回退到表单数据
    if not name:
        name = form_data.get("deceased_name", "")

    if not name:
        return prompt

    # 如果本身已经是 "张先生"/"张女士" 格式，不处理
    if "先生" in name or "女士" in name:
        return prompt

    # 确定姓氏称呼
    surname_title = "先生"
    if gender in ["女", "female", "f"]:
        surname_title = "女士"
    elif "女" in str(form_data.get("speaker_relation", "")):
        surname_title = "女士"

    # 提取姓氏并替换
    surname_match = re.match(r'^([\u4e00-\u9fa5]{1,2})', name)
    surname = surname_match.group(1) if surname_match else ""

    result = prompt
    if surname and name.startswith(surname):
        replacement = f"{surname}{surname_title}"
        # 防御：确保不产生 "张雪先生" 这种错误结果
        if name != replacement and not replacement.endswith(name[len(surname):]):
            result = prompt.replace(name, replacement)

    if result != prompt:
        svc_logger.info("[desensitize] 人名脱敏: '%s' → '%s'", name, replacement)

    return result


def _get_scenes_from_mv04(mv04_out: Any) -> List[Dict[str, Any]]:
    if not isinstance(mv04_out, dict):
        return []
    sc = mv04_out.get("scenes")
    if isinstance(sc, list):
        return [x for x in sc if isinstance(x, dict)]
    if isinstance(sc, dict):
        return [sc[k] for k in sorted(sc.keys()) if isinstance(sc[k], dict)]
    sb = mv04_out.get("storyboard")
    if isinstance(sb, list):
        return [x for x in sb if isinstance(x, dict)]
    return []


def get_characters(sid: str) -> Dict[str, Any]:
    """返回角色档案：主角（逝者）+ 配角列表（来自 MV03 character_bible）"""
    s = session_store.require(sid)
    form = s.get("form_data", {})
    mv03 = s["mv_outputs"].get("MV03") or {}
    bible = mv03.get("character_bible", {}) if isinstance(mv03, dict) else {}
    dna   = bible.get("character_dna", {}) if isinstance(bible, dict) else {}

    main_name = (
        bible.get("display_name")
        or form.get("deceased_name")
        or bible.get("character_id")
        or "主角"
    )
    main_desc_parts: List[str] = []
    if dna.get("facial_features"):  main_desc_parts.append(f"面部：{dna['facial_features']}")
    if dna.get("body_features"):    main_desc_parts.append(f"体型：{dna['body_features']}")
    if dna.get("clothing_style"):   main_desc_parts.append(f"服装：{dna['clothing_style']}")
    if dna.get("mannerisms"):       main_desc_parts.append(f"神态：{dna['mannerisms']}")
    if not main_desc_parts and form.get("occupation"):
        main_desc_parts.append(form["occupation"])

    main_role = {
        "name": main_name,
        "role_label": f"主角 · 逝者（{form.get('speaker_relation','至亲')}）" if form.get("speaker_relation") else "主角 · 逝者",
        "description": "；".join(main_desc_parts) or "（暂无详细外貌描述）",
        "photo_url": "",
    }

    # 配角：优先 MV03 supporting_cast，其次 cast_roles
    cast_raw = []
    if isinstance(mv03, dict):
        cast_raw = mv03.get("supporting_cast") or mv03.get("cast_roles") or []
    supporting: List[Dict[str, Any]] = []
    if isinstance(cast_raw, list):
        for c in cast_raw:
            if not isinstance(c, dict): continue
            supporting.append({
                "name":        c.get("name") or c.get("display_name") or "未命名",
                "role_label":  c.get("role_label") or c.get("relation") or "配角",
                "description": c.get("description") or c.get("desc") or "",
                "photo_url":   c.get("photo_url") or "",
            })

    return {"main": main_role, "supporting": supporting}


def _select_reference_photo(s: dict, scene: dict, scene_idx: int) -> Optional[str]:
    """根据分镜内容和场景时期，从用户上传的照片中选择最合适的一张作为参考。

    优先级：
    1. 分镜中明确指定的照片（通过 scene["_reference_photo"] 由前端指定）
    2. 根据分镜时期自动匹配（如"年轻时"→ period_label="青年" 的照片）
    3. subject="deceased" 的最新张照片（按文件修改时间）
    4. 任意照片（如果没有逝者照片，按文件修改时间）
    """
    from pathlib import Path

    assets = s.get("assets", [])
    # 只取图片，不包含视频
    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    photos = [a for a in assets if a.get("filename", "").lower().endswith(image_exts)]

    if not photos:
        return None

    def get_mtime(photo: dict) -> float:
        """获取照片文件的修改时间，文件不存在则返回 0"""
        fpath = UPLOADS_DIR / photo.get("saved_as", "")
        return fpath.stat().st_mtime if fpath.exists() else 0

    # 按修改时间倒序（最新的在前）
    photos = sorted(photos, key=get_mtime, reverse=True)

    # 1. 前端指定（URL 匹配 saved_as）
    specified = scene.get("_reference_photo")
    if specified:
        match = next((p for p in photos if p.get("url") == specified), None)
        if match:
            return match["saved_as"]

    # 2. 时期匹配（根据分镜描述中的关键词匹配时期）
    scene_desc = (scene.get("description") or scene.get("visual") or "").lower()
    period_keywords = {
        "青年": ["青年", "年轻", "大学", "青春", "插队", "知青"],
        "中年": ["中年", "工作", "车间", "技术", "工程师"],
        "老年": ["老年", "退休", "晚年", "白发"],
    }

    for label, keywords in period_keywords.items():
        if any(kw in scene_desc for kw in keywords):
            match = next(
                (p for p in photos if p.get("period_label") == label and p.get("subject") == "deceased"),
                None,
            )
            if match:
                return match["saved_as"]

    # 3. 默认：逝者的最新��张照片（已按修改时间排序）
    deceased_photo = next((p for p in photos if p.get("subject") == "deceased"), None)
    if deceased_photo:
        return deceased_photo["saved_as"]

    return None


def gen_scene_image(sid: str, scene_idx: int, force: bool = False) -> Dict[str, Any]:
    """为单个分镜生成图片。写入本地磁盘，返回可访问的 URL。

    force=False（首次"生成图片"）：入参一致时返回缓存
    force=True（"重新生成图片"）：始终重新调用 API
    """
    s = session_store.require(sid)
    mv04 = s["mv_outputs"].get("MV04")
    mv03 = s["mv_outputs"].get("MV03")
    scenes = _get_scenes_from_mv04(mv04)
    if scene_idx < 0 or scene_idx >= len(scenes):
        return {"error": True, "message": f"无效的分镜索引 {scene_idx}"}
    scene = scenes[scene_idx]

    # ── Playback 模式：命中本地图片缓存则跳过 API ──
    if _CACHE_MODE == "playback" and not force:
        cached_url = scene.get("_image_url", "")
        # 内存中没有 _image_url → 按约定文件名回退检查磁盘
        if not cached_url:
            fallback_name = f"{sid}_scene{scene_idx}.png"
            fallback_path = GEN_IMAGES_DIR / fallback_name
            if fallback_path.is_file():
                cached_url = f"/api/outputs/generated/images/{fallback_name}"
        if cached_url:
            # 将 URL 路径映射为磁盘文件
            if cached_url.startswith("/api/outputs/generated/images/"):
                cached_path = GEN_IMAGES_DIR / Path(cached_url).name
            else:
                cached_path = Path(cached_url)
            if cached_path.is_file():
                return {"url": cached_url, "cached": True}
        # 构造图片 prompt：优先 build_scene_prompts，失败则用 description 兜底
    try:
        prompts = build_scene_prompts(scene, character_bible=mv03 if isinstance(mv03, dict) else None, use_cache=not force)
        image_prompt = prompts.get("image_prompt") or scene.get("prompt_start") or scene.get("description") or ""
    except Exception:
        image_prompt = scene.get("prompt_start") or scene.get("description") or scene.get("visual") or str(scene)

    if not image_prompt:
        return {"error": True, "message": "无法构造图片 prompt"}

    ref_saved_as =  _select_reference_photo(s, scene, scene_idx)

    # ── 入参缓存检查：非 force 模式下，入参一致且已有图片则直接返回 ──
    if not force:
        cached_url = scene.get("_image_url", "")
        cached_prompt = scene.get("_image_prompt", "")
        cached_ref = scene.get("_reference_photo", "")
        if cached_url and cached_prompt == image_prompt and cached_ref == ref_saved_as:
            img_path = GEN_IMAGES_DIR / Path(cached_url).name if cached_url.startswith("/api/outputs/generated/images/") else Path(cached_url)
            if img_path.is_file():
                return {"url": cached_url, "cached": True}

    # ── 选择参考照片 ──
    reference_b64 = None
    if ref_saved_as:
        import base64 as _b64
        asset_path = UPLOADS_DIR / ref_saved_as
        if asset_path.exists():
            reference_b64 = _b64.b64encode(asset_path.read_bytes()).decode()

    b64, err = generate_image_302(image_prompt, reference_b64=reference_b64)
    if not b64:
        return {"error": True, "message": err or "图片生成失败"}

    # 持久化到磁盘
    local_url = _save_generated_image(b64, sid, scene_idx)
    scene["_image_url"] = local_url
    scene["_image_prompt"] = image_prompt
    scene["_reference_photo"] = ref_saved_as or ""
    session_store.update(sid)
    return {"url": local_url}


def gen_scene_video(sid: str, scene_idx: int, image_url: str = "", force: bool = False) -> Dict[str, Any]:
    """为单个分镜生成视频。下载至本地磁盘，返回可访问的 URL。

    force=False（首次"生成视频"）：入参一致时返回缓存
    force=True（"重新生成视频"）：始终重新调用 API
    """
    s = session_store.require(sid)
    mv04 = s["mv_outputs"].get("MV04")
    mv03 = s["mv_outputs"].get("MV03")
    scenes = _get_scenes_from_mv04(mv04)
    if scene_idx < 0 or scene_idx >= len(scenes):
        return {"error": True, "message": f"无效的分镜索引 {scene_idx}"}
    scene = scenes[scene_idx]

    # ── Playback 模式：非 force 时命中本地视频缓存则跳过 API ──
    if _CACHE_MODE == "playback" and not force:
        cached_url = scene.get("_video_url", "")
        svc_logger.debug("[video playback] cached_url=%s", cached_url)
        # 内存中没有 _video_url → 按约定文件名回退检查磁盘
        if not cached_url:
            fallback_name = f"{sid}_scene{scene_idx}.mp4"
            fallback_path = GEN_VIDEOS_DIR / fallback_name
            if fallback_path.is_file():
                cached_url = f"/api/outputs/generated/videos/{fallback_name}"
        if cached_url:
            if cached_url.startswith("/api/outputs/generated/videos/"):
                cached_path = GEN_VIDEOS_DIR / Path(cached_url).name
            else:
                cached_path = Path(cached_url)
            if cached_path.is_file():
                return {"url": cached_url, "cached": True, "status": "done"}

    image_url = image_url or scene.get("_image_url", "")
    if not image_url:
        return {"error": True, "message": "请先生成首帧图片"}

    # ── 入参缓存检查：非 force 模式下，已有完成视频且版本一致则返回缓存 ──

    if not force:
        existing_video_url = scene.get("_video_url")
        existing_img_ver = scene.get("_video_input_version", 0)
        current_ver = scene.get("_image_version", 0)
        if existing_video_url and existing_img_ver == current_ver:
            v_path = GEN_VIDEOS_DIR / Path(existing_video_url).name if existing_video_url.startswith("/api/outputs/generated/videos/") else Path(existing_video_url)
            if v_path.is_file():
                return {"url": existing_video_url, "cached": True, "status": "done"}

    # ── 复用 task_id：已有待处理任务且版本未变（排除已失败的任务）──
    existing_task_id = scene.get("_video_task_id")
    if existing_task_id and not scene.get("_video_url") and not force:
        if scene.get("_video_status") not in ("failed", "error"):
            current_ver = scene.get("_image_version", 0)
            existing_ver = scene.get("_video_input_version", 0)
            if current_ver == existing_ver:
                # 先查询外部服务确认任务状态，避免复用已死任务
                task_source = scene.get("_video_task_source", "302ai")
                try:
                    if task_source == "302ai":
                        verify_res = generate_video_302ai_i2v(
                            prompt="", image_b64_or_url="",
                            poll=True, max_wait=0, _task_id_only=existing_task_id,
                        )
                    else:
                        verify_res = generate_video_kling(
                            prompt="", image_url="",
                            poll=True, max_wait=0, _task_id_only=existing_task_id,
                        )
                    if verify_res.get("url"):
                        # 任务已完成：更新缓存并返回
                        local_url = _download_generated_video(verify_res["url"], sid, scene_idx)
                        final_url = local_url or verify_res["url"]
                        scene["_video_url"] = final_url
                        scene["_video_task_id"] = existing_task_id
                        scene["_video_task_source"] = task_source
                        scene["_video_status"] = "done"
                        session_store.update(sid)
                        return {"url": final_url, "cached": True, "status": "done"}
                    elif verify_res.get("error") and "超时" not in str(verify_res["error"]):
                        # 任务已失败：清掉旧数据，走下面的新提交流程
                        svc_logger.info("[video] 复用检查：旧 task 已失败，重新提交 sid=%s scene=%d", sid, scene_idx)
                except Exception as e:
                    svc_logger.warning("[video] 复用检查查询失败，重新提交 sid=%s scene=%d err=%s", sid, scene_idx, e)
                # 仍在处理中 / 查询失败 → 清掉旧数据，重新提交
                scene.pop("_video_task_id", None)
                scene.pop("_video_task_source", None)
                scene.pop("_video_status", None)
                session_store.update(sid)

    # ── force 模式：清掉旧视频缓存，确保轮询走 API ──
    if force:
        scene.pop("_video_url", None)
        scene.pop("_video_status", None)
        scene.pop("_video_input_version", None)
        scene.pop("_video_task_id", None)
        scene.pop("_video_task_source", None)
        session_store.update(sid)

    # 相对路径（/api/outputs/generated/images/xxx.png）→ 上传图床获取公网 URL
    if image_url.startswith("/api/outputs/generated/images/"):
        import base64 as _b64
        import requests as _req
        filename = image_url.rsplit("/", 1)[-1]
        local_path = GEN_IMAGES_DIR / filename
        if not local_path.exists():
            return {"error": True, "message": f"首帧图文件不存在：{local_path}"}
        b64 = _b64.b64encode(local_path.read_bytes()).decode()
        try:
            r = _req.post(
                "https://freeimage.host/api/1/upload",
                data={"key": "6d207e02198a847aa98d0a2a901485a5", "source": b64, "format": "json"},
                timeout=30,
            )
            r.raise_for_status()
            rj = r.json()
            image_url = rj["image"]["url"]
            svc_logger.info("[video] 首帧图已上传图床: %s", image_url)
        except Exception as e:
            return {"error": True, "message": f"首帧图上传图床失败：{e}"}

    # 视频 prompt
    try:
        prompts = build_scene_prompts(scene, character_bible=mv03 if isinstance(mv03, dict) else None)
        video_prompt = prompts.get("video_prompt") or scene.get("prompt_video") or scene.get("description") or ""
    except Exception:
        video_prompt = scene.get("prompt_video") or scene.get("description") or ""

    if not video_prompt:
        video_prompt = "电影感长镜头，温暖怀旧的追思氛围，缓慢推进，自然光。"

    # 人名脱敏，避免可灵等平台的内容审核
    form_data = s.get("form_data", {})
    mv03_dict = mv03 if isinstance(mv03, dict) else None
    video_prompt = _desensitize_name_in_prompt(video_prompt, form_data, mv03_dict)

    # 异步提交（poll=False），立即返回 task_id，由前端轮询
    res = generate_video_kling(
        prompt=video_prompt,
        image_url=image_url,
        duration=5,
        poll=False,
    )
    if res.get("error"):
        return {"error": True, "message": res.get("error")}

    task_id = res.get("task_id")
    source  = res.get("source", "302ai")
    if not task_id:
        return {"error": True, "message": f"视频提交未返回 task_id：{res}"}

    # 把 task_id 存入 scene，方便后续轮询时定位
    scene["_video_task_id"]     = task_id
    scene["_video_task_source"] = source
    scene["_video_status"]      = "pending"
    scene["_video_input_version"]   = scene.get("_image_version", 0)
    session_store.update(sid)
    svc_logger.info("[video] 提交成功 task_id=%s source=%s sid=%s scene=%d", task_id, source, sid, scene_idx)
    return {"task_id": task_id, "source": source, "status": "pending"}


def get_cached_scene_video(sid: str, scene_idx: int) -> Optional[str]:
    """检查本地是否有已缓存的视频文件，有则返回 URL，否则返回 None。"""
    try:
        s = session_store.require(sid)
        mv04 = s["mv_outputs"].get("MV04")
        scenes = _get_scenes_from_mv04(mv04)
        if 0 <= scene_idx < len(scenes):
            cached_url = scenes[scene_idx].get("_video_url", "")
            if cached_url:
                cached_path = GEN_VIDEOS_DIR / Path(cached_url).name
                if cached_path.is_file():
                    return cached_url
    except Exception:
        pass
    # 按约定文件名回退
    fallback_name = f"{sid}_scene{scene_idx}.mp4"
    fallback_path = GEN_VIDEOS_DIR / fallback_name
    if fallback_path.is_file():
        return f"/api/outputs/generated/videos/{fallback_name}"
    return None


def recover_pending_videos(sid: str) -> None:
    """返回分镜数据前，自动检查有 task_id 但无 video_url 的分镜，主动查询 302AI 补结果。"""
    try:
        s = session_store.require(sid)
        mv04 = s["mv_outputs"].get("MV04")
        scenes = _get_scenes_from_mv04(mv04)
        if not scenes:
            return
    except Exception:
        return

    for i, sc in enumerate(scenes):
        task_id = sc.get("_video_task_id")
        if not task_id or sc.get("_video_url"):
            continue  # 无任务或已有结果，跳过

        source = sc.get("_video_task_source", "302ai")
        try:
            res = poll_scene_video(task_id, source, sid, i)
            if res.get("status") == "done":
                svc_logger.info("[recover] 补全视频 sid=%s scene=%d url=%s", sid, i, res["url"])
        except Exception as e:
            svc_logger.warning("[recover] 查询失败 sid=%s scene=%d task_id=%s err=%s", sid, i, task_id, e)


def poll_scene_video(task_id: str, source: str, sid: str, scene_idx: int, image_url: str = "") -> Dict[str, Any]:
    """轮询视频任务状态。完成后下载到本地并更新 scene。
    返回:
      处理中 → {"status": "processing", "task_id": ...}
      完成   → {"status": "done", "url": "/api/outputs/..."}
      失败   → {"status": "failed", "message": ...}
    """
    # 先检查 session 中是否已有完成视频（避免重复轮询同一 task_id）
    try:
        s = session_store.require(sid)
        mv04 = s["mv_outputs"].get("MV04")
        scenes = _get_scenes_from_mv04(mv04)
        if 0 <= scene_idx < len(scenes):
            scene = scenes[scene_idx]
            done_url = scene.get("_video_url")
            done_tid = scene.get("_video_task_id")
            if done_url and done_tid == task_id:
                return {"status": "done", "url": done_url}
    except Exception:
        pass

    # 单次轮询（max_wait=0 → 只查一次状态）
    if source == "302ai":
        res = generate_video_302ai_i2v(
            prompt="", image_b64_or_url="",
            poll=True, max_wait=0, _task_id_only=task_id,
        )
    else:
        res = generate_video_kling(
            prompt="", image_url="",
            poll=True, max_wait=0, _task_id_only=task_id,
        )

    if res.get("error"):
        err = res["error"]
        # 超时 = 仍在处理中，不是真正失败
        if "超时" in str(err):
            return {"status": "processing", "task_id": task_id, "source": source}
        # 真正失败：清除 task_id 等元数据，避免下次重试时复用旧 task_id
        try:
            s = session_store.require(sid)
            mv04 = s["mv_outputs"].get("MV04")
            scenes = _get_scenes_from_mv04(mv04)
            if 0 <= scene_idx < len(scenes):
                scene = scenes[scene_idx]
                scene.pop("_video_task_id", None)
                scene.pop("_video_task_source", None)
                scene["_video_status"] = "failed"
            session_store.update(sid)
        except Exception:
            pass
        return {"status": "failed", "message": err}

    cloud_url = res.get("url")
    if not cloud_url:
        return {"status": "processing", "task_id": task_id, "source": source}

    # 下载到本地
    local_url = _download_generated_video(cloud_url, sid, scene_idx)
    final_url = local_url or cloud_url

    # 更新 scene 缓存
    try:
        s = session_store.require(sid)
        mv04 = s["mv_outputs"].get("MV04")
        scenes = _get_scenes_from_mv04(mv04)
        if 0 <= scene_idx < len(scenes):
            scenes[scene_idx]["_video_url"]    = final_url
            scenes[scene_idx]["_video_status"] = "done"
            scenes[scene_idx]["_video_input_version"]   = scenes[scene_idx].get("_image_version", 0)
        session_store.update(sid)
    except Exception:
        pass

    svc_logger.info("[video] 完成 task_id=%s url=%s", task_id, final_url)
    return {"status": "done", "url": final_url}


# ── TTS 合成 ──────────────────────────────────────────────────────────
import hashlib as _hashlib
import io as _io
import uuid as _uuid
import wave as _wave


def _get_wav_duration(data: bytes) -> float:
    """从 WAV 数据读取时长（秒）。"""
    try:
        with _wave.open(_io.BytesIO(data), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0 and frames * 2 < len(data) + 1000:
                return frames / rate
            # ChunkSize=-1 降级：用文件大小减 header 估算
            pcm = data[44:] if data[:4] == b"RIFF" else data
            return len(pcm) / (rate * 2) if rate > 0 else 0
    except Exception:
        # mp3 降级
        try:
            from mutagen.mp3 import MP3
            p = _io.BytesIO(data)
            audio = MP3(p)
            return audio.info.length if audio.info else 0
        except Exception:
            return 0


def _tts_cache_path(text: str) -> Optional[Path]:
    """根据文本 hash 查找已缓存的 TTS 音频文件。"""
    text_hash = _hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    pattern = f"tts_cache_{text_hash}_*.wav"
    hits = list(AUDIO_OUTPUT_DIR.glob(pattern))
    return hits[0] if hits else None


def synthesize_tts_text(text: str, sid: str, scene_idx: int = 0) -> Optional[tuple]:
    """将纯文本合成为 WAV，带缓存：相同文本复用已生成的文件。"""
    text = (text or "").strip()
    if not text:
        return None

    # 1) 查缓存
    cached = _tts_cache_path(text)
    if cached and cached.exists():
        audio_bytes = cached.read_bytes()
        url = f"/api/outputs/generated/audio/{cached.name}"
        duration = _get_wav_duration(audio_bytes)
        svc_logger.info("[tts] cache hit sid=%s scene=%d file=%s", sid, scene_idx, cached.name)
        return url, duration

    # 2) 生成
    audio_bytes = seed_tts(text)
    if not audio_bytes:
        svc_logger.warning("[tts] synthesis returned no audio for text: %s", text[:80])
        return None

    text_hash = _hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    filename = f"tts_cache_{text_hash}_{_uuid.uuid4().hex[:6]}.wav"
    path = AUDIO_OUTPUT_DIR / filename
    path.write_bytes(audio_bytes)
    url = f"/api/outputs/generated/audio/{filename}"
    duration = _get_wav_duration(audio_bytes)
    svc_logger.info("[tts] sid=%s scene=%d file=%s size=%d dur=%.1fs", sid, scene_idx, filename, len(audio_bytes), duration)
    return url, duration


def generate_tts_segments(sid: str, scenes: list) -> list:
    """从分镜中提取旁白文本并逐段合成 TTS。
    同时更新 scene 对象上的 _tts_audio_url / _tts_duration_sec。
    返回 tts_segments 列表，用于存入 session['audio']['tts_segments']。
    """
    tts_segments: list = []
    for i, sc in enumerate(scenes):
        narr = (sc.get("voice_script")  or "").strip()
        if narr:
            result = synthesize_tts_text(narr, sid, i)
            if result:
                url, duration = result
                sc["_tts_audio_url"] = url
                sc["_tts_duration_sec"] = round(duration, 1)
                tts_segments.append({"scene_idx": i, "audio_url": url, "duration_sec": round(duration, 1)})
    return tts_segments


def generate_bgm(sid: str, force: bool = False) -> dict:
    """为 session 生成 BGM（分析情感 → 生成/缓存 → 存入 session）。
    返回 bgm_result dict（含 emotion, bgm_url, duration_sec)。
    """
    bgm_result = match_bgm(sid, force=force)
    s = session_store.require(sid)
    s.setdefault("audio", {})
    s["audio"]["bgm"] = bgm_result
    session_store.update(sid)
    return bgm_result


# ── 视频拼接（moviepy）────────────────────────────────────────────────
# 参考 mini-pipeline/run.py step4()

_WINDOWS_CHINESE_FONTS = [
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/simkai.ttf",     # 楷体
]
_LINUX_CHINESE_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansSC-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansSC-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttf",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
]

def _find_chinese_font() -> Optional[str]:
    # 优先使用项目自带字体
    bundled = ROOT_DIR / "backend" / "assets" / "fonts" / "NotoSansSC-Regular.otf"
    if bundled.exists():
        return str(bundled)
    # 再查系统字体
    for p in _LINUX_CHINESE_FONTS + _WINDOWS_CHINESE_FONTS:
        if Path(p).exists():
            return p
    return None


def _format_srt_time(seconds: float) -> str:
    """将秒数转换为 SRT 时间格式 HH:MM:SS,mmm"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def _url_to_local_path(url: str) -> Optional[Path]:
    """将 /api/outputs/generated/audio/xxx.wav → 本地文件路径。"""
    if not url or not url.startswith("/api/outputs/generated/"):
        return None
    # /api/outputs/generated/... → backend/outputs/generated/...
    rel = url.replace("/api/outputs/generated/", "backend/outputs/generated/", 1)
    return ROOT_DIR / rel


def _fmt_srt_time(seconds: float) -> str:
    """将秒数转为 SRT 时间戳格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _make_ken_burns_clip(img_path: str, duration: float) -> "VideoClip":
    """将静态图片转为 Ken Burns 缓慢缩放动画视频片段。"""
    from PIL import Image as PIL_Image
    from moviepy import ImageClip
    import numpy as _np

    img_clip = ImageClip(img_path).with_duration(duration).with_fps(24)
    w, h = img_clip.size

    def zoom_effect(get_frame, t):
        ratio = 1 + 0.15 * (t / duration)
        new_w, new_h = int(w * ratio), int(h * ratio)
        frame = PIL_Image.fromarray(get_frame(t))
        frame = frame.resize((new_w, new_h), PIL_Image.LANCZOS)
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        frame = frame.crop((left, top, left + w, top + h))
        return _np.array(frame)

    return img_clip.transform(zoom_effect, apply_to=[])


def _build_subtitle_clip(scenes: list, scene_durations: list, scene_starts: list, video_size: tuple):
    """为所有分镜生成字幕轨道（底部居中，白色+黑色描边）。"""
    from moviepy import TextClip, CompositeVideoClip, ColorClip

    font = _find_chinese_font()
    if font is None:
        svc_logger.warning("[subtitle] 未找到中文字体，字幕可能显示异常")
        font = "Arial"

    subtitle_clips = []
    total_dur = 0

    for scene, dur, start in zip(scenes, scene_durations, scene_starts):
        text = (scene.get("voice_script") or scene.get("narration") or scene.get("subtitle") or "").strip()
        end = start + dur
        if end > total_dur:
            total_dur = end
        if not text or dur <= 0:
            continue

        try:
            txt_clip = (
                TextClip(
                    text=text,
                    font_size=36,
                    color="white",
                    font=font,
                    stroke_color="black",
                    stroke_width=2,
                    method="caption",
                    text_align="center",
                    size=(int(video_size[0] * 0.85), None),
                )
                .with_start(start)
                .with_duration(dur)
                .with_position(("center", video_size[1] - 120))
            )
            subtitle_clips.append(txt_clip)
        except Exception as e:
            svc_logger.warning("[subtitle] 跳过 scene: %s", e)

    if not subtitle_clips:
        return None

    bg = ColorClip(size=video_size, color=(0, 0, 0, 0), duration=total_dur)
    return CompositeVideoClip([bg] + subtitle_clips, size=video_size)


def _extend_video(clip, target_duration):
    """用 ffmpeg minterpolate 智能补帧延长视频到目标时长"""
    import subprocess
    from moviepy import VideoFileClip
    from moviepy.config import FFMPEG_BINARY

    speed = clip.duration / target_duration

    if speed >= 0.95:
        return clip  # 差异很小，直接使用

    src = clip.filename
    base = Path(src).stem
    parent = Path(src).parent
    out_file = parent / f"_interp_{base}.mp4"

    subprocess.run(
        [
            FFMPEG_BINARY, "-y", "-i", src,
            "-filter_complex",
            f"setpts=PTS/{speed},minterpolate='mi_mode=blend:fps=24'",
            "-c:v", "libx264", "-an",
            str(out_file),
        ],
        capture_output=True,
        encoding='utf-8', errors='replace',
    )

    new_clip = VideoFileClip(str(out_file))
    if new_clip.duration > target_duration + 0.05:
        new_clip = new_clip.subclipped(0, target_duration)
    return new_clip


def _extend_video_file(src_path: str, target_duration: float) -> str:
    """用 ffmpeg minterpolate 补帧延长视频文件，返回输出文件路径。"""
    import subprocess
    from moviepy.config import FFMPEG_BINARY
    from moviepy import VideoFileClip

    clip = VideoFileClip(src_path)
    speed = clip.duration / target_duration
    clip.close()

    if speed >= 0.95:
        return src_path

    base = Path(src_path).stem
    parent = Path(src_path).parent
    out_file = parent / f"_interp_{base}.mp4"

    result = subprocess.run(
        [
            FFMPEG_BINARY, "-y", "-i", src_path,
            "-filter_complex",
            f"setpts=PTS/{speed},minterpolate='mi_mode=blend:fps=24'",
            "-c:v", "libx264", "-an",
            str(out_file),
        ],
        capture_output=True,
        encoding='utf-8', errors='replace',
    )

    if result.returncode != 0:
        svc_logger.error("[extend_video] ffmpeg 补帧失败: %s", result.stderr[-200:] if result.stderr else "")
        return src_path

    return str(out_file)

 
def assemble_final_video(s: dict) -> Dict[str, Any]:
    """拼接最终视频：视频/图片 + 旁白 + BGM + 字幕。
    使用 ffmpeg complex filter 一次性完成，避免 moviepy 逐帧渲染。
    返回 {"ok": bool, "video_url": str, "duration_sec": float, "error": str}
    """
    from moviepy.config import FFMPEG_BINARY
    import subprocess as _subprocess

    _t0 = time.time()
    sid = s["session_id"]
    mv04 = s["mv_outputs"].get("MV04")
    scenes = _get_scenes_from_mv04(mv04)
    audio_info = s.get("audio", {})
    tts_segments = audio_info.get("tts_segments", [])
    bgm_info = audio_info.get("bgm", {})

    # ── 1. 筛选可用分镜（视频 or 图片）──
    _t = time.time()
    usable = []
    for i, sc in enumerate(scenes):
        entry = dict(sc)
        entry["_idx"] = i

        vid_found = False
        if sc.get("_video_url"):
            p = _url_to_local_path(sc["_video_url"])
            if p and p.exists():
                entry["video_path"] = str(p)
                entry["video_mode"] = "video"
                vid_found = True

        if not vid_found:
            img_url = sc.get("_img_url") or sc.get("_image_url")
            if img_url:
                p = _url_to_local_path(img_url)
                if p and p.exists():
                    entry["image_path"] = str(p)
                    entry["video_mode"] = "ken_burns"
                    usable.append(entry)
                    continue

        if vid_found:
            usable.append(entry)

    if not usable:
        return {"ok": False, "error": "没有可用的分镜（图片/视频均未找到）"}

    audio_map = {}
    for t_seg in tts_segments:
        idx = t_seg.get("scene_idx")
        p = _url_to_local_path(t_seg.get("audio_url", ""))
        if p and p.exists():
            audio_map[idx] = {"path": str(p), "duration": t_seg.get("duration_sec", 0)}

    bgm_path = _url_to_local_path(bgm_info.get("bgm_url", ""))
    if bgm_path and not bgm_path.exists():
        bgm_path = None

    _d_filter = round(time.time() - _t, 1)
    svc_logger.info("[assemble] sid=%s usable_scenes=%d audio_tracks=%d bgm=%s (筛选 %.1fs)",
                    sid, len(usable), len(audio_map), "yes" if bgm_path else "no", _d_filter)

    try:
        # ── 2. 计算各分镜时长和处理 ──
        _t = time.time()
        OUT_W, OUT_H = 1280, 720
        fps = 24
        total_dur = 0.0
        scene_starts = []

        for entry in usable:
            i = entry["_idx"]
            audio_dur = audio_map.get(i, {}).get("duration", 0) or 0
            if entry.get("video_mode") == "ken_burns":
                dur = max(5.0, audio_dur) if audio_dur > 0 else 5.0
                entry["_target_dur"] = dur
            else:
                from moviepy import VideoFileClip
                vid_dur = VideoFileClip(entry["video_path"]).duration
                target_dur = max(vid_dur, audio_dur) if audio_dur > 0 else vid_dur
                target_dur = max(target_dur, 1.0)

                if vid_dur < target_dur - 0.1:
                    # 视频短于目标 → minterpolate 补帧延长
                    svc_logger.info("[assemble] scene%d 视频 %.1fs < %.1fs，执行补帧", i, vid_dur, target_dur)
                    interp_path = _extend_video_file(entry["video_path"], target_dur)
                    if interp_path:
                        entry["_video_path"] = interp_path
                    dur = target_dur
                elif vid_dur > target_dur + 0.1:
                    dur = target_dur
                else:
                    dur = vid_dur
                entry["_target_dur"] = dur

            scene_starts.append(total_dur)
            total_dur += dur
            mode_str = "Ken Burns" if entry.get("video_mode") == "ken_burns" else "视频"
            svc_logger.info("[assemble] scene%d %s %.1fs (音频 %.1fs)", i, mode_str, dur, audio_dur)

        _d_calc = round(time.time() - _t, 1)
        svc_logger.info("[assemble] 计算时长完成 (%.1fs) 总时长 %.1fs", _d_calc, total_dur)

        # ── 3. 构建 ffmpeg 命令 ──
        _t = time.time()
        cmd = [FFMPEG_BINARY, "-y"]
        filters = []
        filter_idx = 0  # ffmpeg -i 输入序号
        video_labels = []

        # 视频/图片输入 + Ken Burns 或缩放
        for entry in usable:
            if entry.get("video_mode") == "ken_burns":
                cmd += ["-loop", "1", "-i", entry["image_path"]]
                dur = entry["_target_dur"]
                dur_frames = int(dur * fps)
                # zoompan: 缓慢放大 15%，居中裁剪
                zoompan = (
                    f"[{filter_idx}]format=yuv420p,"
                    f"zoompan=z='min(1+0.15*on/{dur_frames},1.15)':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d=1:fps={fps}:s={OUT_W}x{OUT_H}:duration={dur}"
                    f"[v{filter_idx}]"
                )
                filters.append(zoompan)
            else:
                vid_path = entry.get("_video_path", entry["video_path"])
                cmd += ["-i", vid_path]
                dur = entry["_target_dur"]
                trim = f"[{filter_idx}]trim=duration={dur:.3f},setpts=PTS-STARTPTS,scale={OUT_W}:{OUT_H},format=yuv420p[v{filter_idx}]"
                filters.append(trim)
            video_labels.append(f"[v{filter_idx}]")
            filter_idx += 1

        # 为每个分镜添加淡入淡出效果
        # 场景开头淡入（0.3秒），场景结尾淡出（0.3秒）
        fade_dur = 0.3
        faded_labels = []
        for idx, (label, entry) in enumerate(zip(video_labels, usable)):
            scene_dur = entry["_target_dur"]
            fade_label = f"[vf{idx}]"

            # 头部淡入 + 尾部淡出（alpha 透明叠加实现）
            fade_in_dur = min(fade_dur, scene_dur)
            fade_out_start = max(0, scene_dur - fade_dur)

            fade_filter = (
                f"{label}fade=t=in:st=0:d={fade_in_dur}:alpha=1,"
                f"fade=t=out:st={fade_out_start}:d={scene_dur - fade_out_start}:alpha=1{fade_label}"
            )
            filters.append(fade_filter)
            faded_labels.append(fade_label)

        # 视频 concat
        concat_label = f"{''.join(faded_labels)}concat=n={len(usable)}:v=1:a=0[outv]"
        filters.append(concat_label)
        final_v_label = "[outv]"

        # 音频处理
        audio_labels = []
        audio_idx_start = len(usable)  # 音频输入的起始序号

        # 旁白音频
        has_voiceover = False
        audio_labels = []
        for entry in usable:
            i = entry["_idx"]
            if i in audio_map:
                has_voiceover = True
                audio_path = audio_map[i]["path"]
                cmd += ["-i", audio_path]
                start_ms = scene_starts[i] * 1000.0
                dur = entry["_target_dur"]
                atrim = f"[{audio_idx_start + len(audio_labels)}]atrim=end={dur:.3f},asetpts=PTS-STARTPTS,adelay={start_ms}|{start_ms}:all=1[a{len(audio_labels)}]"
                filters.append(atrim)
                audio_labels.append(f"[a{len(audio_labels)}]")

        # BGM
        has_bgm = bgm_path is not None
        if has_bgm:
            cmd += ["-i", str(bgm_path)]
            bgm_audio_idx = audio_idx_start + len(audio_labels)
            bgm_volume = 0.2 if has_voiceover else 0.3
            # BGM 足够长时直接截断，不循环；不够长时才循环+淡入淡出
            bgm_filter = f"[{bgm_audio_idx}]atrim=end={total_dur:.3f},asetpts=PTS-STARTPTS,volume={bgm_volume}[abgm]"
            filters.append(bgm_filter)
            audio_labels.append("[abgm]")

        # 混音
        if audio_labels:
            inputs = "".join(audio_labels)
            amix = f"{inputs}amix=inputs={len(audio_labels)}:duration=longest[outa]"
            filters.append(amix)
            final_a_label = "[outa]"
        else:
            final_a_label = None

        # 字幕方案：drawtext + fontfile + textfile
        #   每个字幕段落一个 UTF-8 文本文件，drawtext 从文件读取
        #   优势：fontfile 直接指定字体路径，不依赖 fontsdir/fontconfig
        font = _find_chinese_font()
        import tempfile
        import shutil as _shutil
        import os as _os
        temp_dir = None
        srt_path = None
        has_subtitles = any(
            (sc.get("voice_script") or sc.get("narration") or sc.get("subtitle") or "").strip()
            for sc in usable
        )
        if has_subtitles and font:
            try:
                _temp_base = OUTPUTS_DIR / "temp"
                _temp_base.mkdir(parents=True, exist_ok=True)
                temp_dir = tempfile.mkdtemp(prefix="niannian_sub_", dir=str(_temp_base))
                srt_path = _os.path.join(temp_dir, "subs.srt")
                idx = 0
                srt_lines = []
                for entry in usable:
                    i = entry["_idx"]
                    text = (entry.get("voice_script") or entry.get("narration") or entry.get("subtitle") or "").strip()
                    if text:
                        start = scene_starts[i]
                        end = start + entry["_target_dur"]
                        srt_lines.append(f"{idx + 1}")
                        srt_lines.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
                        srt_lines.append(text)
                        srt_lines.append("")
                        idx += 1
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(srt_lines))
                svc_logger.info("[assemble] 已生成 SRT 字幕文件 (%d 条)", idx)
            except Exception as e:
                svc_logger.warning("[assemble] 字幕文件创建失败: %s", e)
                srt_path = None
                if temp_dir:
                    _shutil.rmtree(temp_dir, ignore_errors=True)
                    temp_dir = None

        # ── Pass 1: 视频拼接 + 音频混音 ──
        filter_complex = ";".join(filters)
        cmd += ["-filter_complex", filter_complex]
        cmd += ["-map", final_v_label]
        if final_a_label:
            cmd += ["-map", final_a_label]
        cmd += [
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-r", str(fps),
            "-t", f"{total_dur:.3f}",
        ]

        FINAL_DIR.mkdir(parents=True, exist_ok=True)
        out_filename = f"final_{sid}_{int(time.time())}.mp4"
        out_path = FINAL_DIR / out_filename

        if has_subtitles and srt_path:
            # 两遍渲染：Pass 1 输出中间文件
            _tmp_fd, inter_path = tempfile.mkstemp(suffix="_inter.mp4", dir=str(FINAL_DIR))
            _os.close(_tmp_fd)
            cmd_pass1 = cmd + [inter_path]

            svc_logger.info("[assemble] Pass 1: 视频拼接 + 音频混音 (无字幕)")
            _t = time.time()
            result = _subprocess.run(cmd_pass1, capture_output=True, encoding='utf-8', errors='replace')
            _d_pass1 = round(time.time() - _t, 1)

            if result.returncode != 0:
                err_msg = result.stderr[-500:] if result.stderr else "unknown"
                svc_logger.error("[assemble] Pass 1 失败: %s", err_msg)
                _os.unlink(inter_path)
                if temp_dir:
                    _shutil.rmtree(temp_dir, ignore_errors=True)
                return {"ok": False, "error": f"ffmpeg Pass 1 失败: {err_msg}"}

            svc_logger.info("[assemble] Pass 1 完成 (%.1fs)", _d_pass1)

            # Pass 2: subtitles 滤镜叠加字幕（-vf 模式，简单可靠，跨平台）
            # 优先使用相对于 ROOT_DIR 的相对路径，避免 Windows 盘符冒号被 FFmpeg filter 解析器误认
            def _to_filter_path(abs_path: str) -> str:
                """尝试转为相对路径；成功则返回 / 分隔的相对路径，否则转义冒号后返回"""
                try:
                    rel = Path(abs_path).relative_to(ROOT_DIR)
                    return str(rel).replace("\\", "/")
                except ValueError:
                    return abs_path.replace("\\", "/").replace(":", "\\:")

            srt_escaped = _to_filter_path(srt_path)
            font_dir = _os.path.dirname(font)
            fonts_dir = _to_filter_path(font_dir)
            font_family = "Source Han Sans SC"
            svc_logger.info(f"srt_escaped：{srt_escaped}")
            svc_logger.info(f"fonts_dir： {fonts_dir}")
            vf_sub = (
                f"subtitles={srt_escaped}:"
                f"fontsdir={fonts_dir}:"
                f"force_style='Fontname={font_family},Fontsize=28,PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=50'"
            )
            cmd2 = [
                FFMPEG_BINARY, "-y",
                "-i", inter_path,
                "-vf", vf_sub,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "copy",
                "-r", str(fps),
                str(out_path),
            ]

            svc_logger.info("[assemble] Pass 2: subtitles 叠加字幕 → %s", out_path)
            _t = time.time()
            result = _subprocess.run(cmd2, capture_output=True, encoding='utf-8', errors='replace')
            _d_pass2 = round(time.time() - _t, 1)

            # 清理中间文件
            _os.unlink(inter_path)
            if temp_dir:
                _shutil.rmtree(temp_dir, ignore_errors=True)

            if result.returncode != 0:
                err_msg = result.stderr[-500:] if result.stderr else "unknown"
                svc_logger.error("[assemble] Pass 2 失败: %s", err_msg)
                return {"ok": False, "error": f"ffmpeg Pass 2 失败: {err_msg}"}

            _d_encode = _d_pass1 + _d_pass2
            svc_logger.info(
                "[assemble] sid=%s 总耗时 %.1fs | 筛选 %.1fs | 时长计算 %.1fs | "
                "Pass1(视频+音频) %.1fs | Pass2(字幕) %.1fs",
                sid, round(time.time() - _t0, 1), _d_filter, _d_calc, _d_pass1, _d_pass2,
            )
        else:
            # 无字幕：直接输出
            cmd.append(str(out_path))

            svc_logger.info("[assemble] ffmpeg 命令已构建 (%.1fs)", round(time.time() - _t, 1))
            svc_logger.info("[assemble] 渲染输出 → %s", out_path)

            _t = time.time()
            result = _subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
            _d_encode = round(time.time() - _t, 1)

            if result.returncode != 0:
                err_msg = result.stderr[-500:] if result.stderr else "unknown"
                svc_logger.error("[assemble] ffmpeg 失败: %s", err_msg)
                return {"ok": False, "error": f"ffmpeg 渲染失败: {err_msg}"}

            svc_logger.info(
                "[assemble] sid=%s 总耗时 %.1fs | 筛选 %.1fs | 时长计算 %.1fs | ffmpeg编码 %.1fs",
                sid, round(time.time() - _t0, 1), _d_filter, _d_calc, _d_encode,
            )

        video_url = f"/api/outputs/final_cuts/{out_filename}"
        return {
            "ok": True,
            "video_url": video_url,
            "duration_sec": round(total_dur, 1),
            "scenes_count": len(usable),
        }

    except Exception as e:
        svc_logger.exception("[assemble] failed: %s", e)
        return {"ok": False, "error": str(e)}


# ── BGM 匹配 ──────────────────────────────────────────────────────────


def _gen_bgm_prompt(style) -> str:
    """根据人物信息和风格偏好，生成 BGM 风格描述。"""
    tags = _BGM_STYLE_MAP.get(style, "warm, nostalgic, gentle, piano, emotional")
    tags += ", memorial, cinematic, instrumental"
    return tags


def match_bgm(sid: str, force: bool = False) -> dict:
    """Suno 生成 BGM（带缓存）→ 返回 {bgm_url, emotion, duration_sec}。"""
    s = session_store.require(sid)
    style = s.get("form_data", {}).get("style_preference", "warm_nostalgia")

    # 1) 查 session 缓存（force=true 时跳过）
    if not force:
        existing_bgm = s.get("audio", {}).get("bgm", {})
        if existing_bgm.get("emotion") == style and existing_bgm.get("bgm_url"):
            cached_path = _url_to_local_path(existing_bgm["bgm_url"])
            if cached_path and cached_path.exists():
                svc_logger.info("[bgm] session cache hit sid=%s emotion=%s", sid, style)
                return existing_bgm

    # 2) 生成
    name = s.get("form_data", {}).get("deceased_name", "未知亲人")
    tags = _gen_bgm_prompt(style)
    audio_bytes = generate_bgm_suno(tags=tags, title=f"追思 · {name}")

    result: Dict[str, Any] = {
        "emotion": style,
        "bgm_url": None,
        "duration_sec": None,
    }

    if not audio_bytes:
        raise ValueError(f"BGM 生成失败：Suno 未返回音频数据 (style={style}, name={name})")

    if audio_bytes:
        ext = "mp3"
        filename = f"bgm_{style}_{_uuid.uuid4().hex[:8]}.{ext}"
        path = AUDIO_OUTPUT_DIR / filename
        path.write_bytes(audio_bytes)
        url = f"/api/outputs/generated/audio/{filename}"
        result["bgm_url"] = url
        result["duration_sec"] = _get_wav_duration(audio_bytes)
        svc_logger.info("[bgm] sid=%s style=%s url=%s size=%d", sid, style, url, len(audio_bytes))

    s.setdefault("audio", {})
    s["audio"]["bgm"] = result
    session_store.update(sid)
    return result


# ── MV06 前置音频流程 ─────────────────────────────────────────────────
def _run_mv06_work(sid: str) -> None:
    """后台执行 MV06 完整流程。结果写入 session pipeline_state。"""
    import traceback

    _timings: dict[str, float] = {}

    # 清除旧的 mv06_result，避免 /status 返回 stale 错误
    try:
        s0 = session_store.require(sid)
        s0.pop("mv06_result", None)
        session_store.update(sid)
    except Exception:
        pass

    def _set_progress(step: str, label: str) -> None:
        """更新 MV06 进度到 session。"""
        try:
            s = session_store.require(sid)
            s["pipeline_state"]["MV06"] = {
                "status": "running",
                "step": step,
                "label": label,
                "duration_sec": None,
                "timings": dict(_timings),
                "error": None,
            }
            session_store.update(sid)
        except Exception:
            pass

    try:
        s = session_store.require(sid)
        gate = s["gate"]

        # 1. 自动批准 MV05 闸门
        _set_progress("approving", "准备中…")
        _t0 = time.time()
        gate_manager.approve(gate, "MV05")
        _timings["approving"] = round(time.time() - _t0, 1)
        s["pipeline_state"]["MV05"] = {"status": "approved", "duration_sec": None, "error": None}
        session_store.update(sid)
        svc_logger.info("[mv06.pre] MV05 gate auto-approved sid=%s (%.1fs)", sid, _timings["approving"])

        # 2. TTS + BGM 并行执行
        _set_progress("tts_running", "TTS语音合成中…")
        _set_progress("bgm_running", "BGM背景音乐匹配中…")
        scenes = _get_scenes_from_mv04(s["mv_outputs"].get("MV04"))
        s.setdefault("audio", {})

        import concurrent.futures
        _tts_exc: list = []
        _bgm_exc: list = []

        def _run_tts():
            try:
                _start = time.time()
                result = generate_tts_segments(sid, scenes)
                elapsed = round(time.time() - _start, 1)
                _timings["tts"] = elapsed
                svc_logger.info("[mv06.pre] TTS completed sid=%s segments=%d (%.1fs)", sid, len(result) if result else 0, elapsed)
                return result
            except Exception as e:
                _timings["tts"] = round(time.time() - _start, 1)
                svc_logger.error("[mv06.pre] TTS failed sid=%s (%.1fs): %s", sid, _timings["tts"], e)
                _tts_exc.append(e)
                return []

        def _run_bgm():
            try:
                _start = time.time()
                force_bgm = s.pop("force_bgm", False)
                if force_bgm:
                    session_store.update(sid)
                result = generate_bgm(sid, force=force_bgm)
                elapsed = round(time.time() - _start, 1)
                _timings["bgm"] = elapsed
                svc_logger.info("[mv06.pre] BGM completed sid=%s style=%s (%.1fs)", sid, result.get("emotion", "?"), elapsed)
                return result
            except Exception as e:
                _timings["bgm"] = round(time.time() - _start, 1)
                svc_logger.error("[mv06.pre] BGM failed sid=%s (%.1fs): %s", sid, _timings["bgm"], e)
                _bgm_exc.append(e)
                return {}

        _t_start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tts_fut = pool.submit(_run_tts)
            bgm_fut = pool.submit(_run_bgm)
            tts_segments = tts_fut.result()
            bgm_result = bgm_fut.result()
        _timings["tts_bgm_total"] = round(time.time() - _t_start, 1)
        svc_logger.info("[mv06.pre] TTS+BGM parallel total sid=%s (%.1fs)", sid, _timings["tts_bgm_total"])

        s["audio"]["tts_segments"] = tts_segments
        if tts_segments:
            session_store.update(sid)
            svc_logger.info("[mv06.pre] TTS synthesized %d segments sid=%s (%.1fs)", len(tts_segments), sid, _timings.get("tts", 0))
        _set_progress("tts_done", "已完成TTS语音合成")

        if _bgm_exc:
            svc_logger.warning("[mv06.pre] BGM 生成失败: %s", _bgm_exc[0])
        else:
            s["audio"]["bgm"] = bgm_result
            session_store.update(sid)
        _set_progress("bgm_done", "已完成BGM背景音乐匹配")

        # 4. 视频拼接
        _set_progress("video_running", "视频合成中…")
        _t0 = time.time()
        video_result = assemble_final_video(s)
        _dur = round(time.time() - _t0, 1)
        _timings["video"] = _dur
        if video_result.get("ok"):
            _set_progress("video_done", "视频合成完成")

        # 附加结果
        if video_result.get("ok"):
            video_result.setdefault("audio", {})
            video_result["audio"]["tts_segments"] = tts_segments
            video_result["audio"]["bgm"] = bgm_result

        _total = round(sum(_timings.values()), 1)

        s["pipeline_state"]["MV06"] = {
            "status": "done" if video_result.get("ok") else "error",
            "step": "done" if video_result.get("ok") else "error",
            "label": "视频合成完成" if video_result.get("ok") else "合成失败",
            "duration_sec": _total,
            "timings": _timings,
            "error": video_result.get("error"),
        }
        s.setdefault("mv06_result", {})
        s["mv06_result"] = video_result
        if video_result.get("ok"):
            s["mv06_result"]["final_video_url"] = video_result["video_url"]
        session_store.update(sid)
        svc_logger.info("[mv06] completed sid=%s ok=%s total=%.1fs timings=%s", sid, video_result.get("ok"), _total, _timings)

    except Exception:
        s = session_store.require(sid)
        tb = traceback.format_exc()
        s["pipeline_state"]["MV06"] = {
            "status": "error",
            "step": "error",
            "label": "合成失败",
            "duration_sec": round(sum(_timings.values()), 1) if _timings else None,
            "timings": _timings,
            "error": tb,
        }
        s["mv06_result"] = {"ok": False, "error": tb}
        session_store.update(sid)
        svc_logger.error("[mv06] background failed sid=%s\n%s", sid, tb)


def run_mv06_with_audio(sid: str) -> None:
    """提交 MV06 流程到后台线程执行，立即返回。"""
    import threading

    threading.Thread(target=_run_mv06_work, args=(sid,), daemon=True).start()
    svc_logger.info("[mv06] background task submitted sid=%s", sid)


# ── 重置步骤 ──────────────────────────────────────────────────────────────

# 每个 MV 步骤对应的磁盘文件清理规则（函数返回 glob pattern 列表）
def _step_file_patterns(sid: str, mv_id: str) -> List[tuple]:
    """返回指定步骤需要清理的文件 (目录, glob) 列表。"""
    patterns: List[tuple] = []
    if mv_id in ("MV01", "MV02", "MV03", "MV04"):
        # MV04 生成的分镜图片和视频
        patterns.append((GEN_IMAGES_DIR, f"{sid}_scene*.png"))
        patterns.append((GEN_VIDEOS_DIR, f"{sid}_scene*.mp4"))
    if mv_id == "MV06":
        patterns.append((FINAL_DIR, f"final_{sid}_*.mp4"))
    return patterns


def reset_step(sid: str, mv_id: str) -> Dict[str, Any]:
    """重置指定 MV 步骤及其后续步骤的状态，清理关联的磁盘文件。"""
    mv_id = mv_id.upper()
    if mv_id not in gate_manager.GATE_ORDER:
        return {"ok": False, "message": f"unknown step: {mv_id}"}

    s = session_store.require(sid)
    pc = s["pipeline_state"].get("pipeline_chain", {})
    if pc.get("status") == "running":
        return {"ok": False, "message": "pipeline 正在运行中，请等待完成或页面刷新后再重置"}

    s = session_store.require(sid)
    idx = gate_manager.GATE_ORDER.index(mv_id)
    downstream = gate_manager.GATE_ORDER[idx:]

    # 1. 重置 gate 状态
    gate_manager.reset_from(s["gate"], mv_id)

    # 2. 重置 pipeline_state
    for step in downstream:
        s["pipeline_state"][step] = {"status": "pending", "duration_sec": None, "error": None}

    # 3. 清除 mv_outputs
    for step in downstream:
        s["mv_outputs"].pop(step, None)

    # 4. 清除附加数据
    if "mv06_result" in s:
        del s["mv06_result"]

    # 5. 清理磁盘文件（仅清理当前 sid 关联的）
    deleted_files = []
    for step in downstream:
        for dir_path, pattern in _step_file_patterns(sid, step):
            for f in dir_path.glob(pattern):
                f.unlink()
                deleted_files.append(str(f))

    session_store.update(sid)
    svc_logger.info("[reset] sid=%s from=%s deleted=%d files", sid, mv_id, len(deleted_files))
    return {"ok": True, "reset_from": mv_id, "deleted_files": deleted_files}
