# backend/services/service_manager.py
# 统一出口 —— routers 只允许从这里 import。
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import gate_manager, session_store  # noqa: F401

from .llm_client import (
    PRIMARY_CLIENT,
    TEXT_MODEL,
    TEXT_FALLBACK_MODEL,
    DIALOGUE_MODEL,
    call_skill,
    call_memorial_chat,
    call_freeform,
    call_structured,
    describe_image,
    transcribe_audio,
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

for _d in (OUTPUTS_DIR, UPLOADS_DIR, FINAL_DIR, GENERATED_DIR, GEN_IMAGES_DIR, GEN_VIDEOS_DIR):
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

        # payload：form_data + 已有 mv 输出
        payload: Dict[str, Any] = {
            "form_data": s["form_data"],
            "mv_outputs": s["mv_outputs"],
        }
        result = call_skill(mv_id, system_prompt, payload)
        elapsed = round(_t.time() - t0, 2)

        if isinstance(result, dict) and result.get("error"):
            gate_manager.reject(gate, mv_id, {})
            s["pipeline_state"][mv_id] = {
                "status": "error", "duration_sec": elapsed, "error": result.get("message", "unknown")
            }
            return {"error": True, "step": mv_id, "message": result.get("message")}

        s["mv_outputs"][mv_id] = result
        gate_manager.approve(gate, mv_id)
        s["pipeline_state"][mv_id] = {"status": "approved", "duration_sec": elapsed, "error": None}

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


# ── 生成资源本地持久化 ─────────────────────────────────────────────────
def _save_generated_image(b64: str, sid: str, scene_idx: int) -> str:
    """将 base64 图片写入磁盘，返回本地 URL。"""
    import base64 as _b64
    filename = f"{sid}_scene{scene_idx}.png"
    path = GEN_IMAGES_DIR / filename
    path.write_bytes(_b64.b64decode(b64))
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


def gen_scene_image(sid: str, scene_idx: int) -> Dict[str, Any]:
    """为单个分镜生成图片。写入本地磁盘，返回可访问的 URL。"""
    s = session_store.require(sid)
    mv04 = s["mv_outputs"].get("MV04")
    mv03 = s["mv_outputs"].get("MV03")
    scenes = _get_scenes_from_mv04(mv04)
    if scene_idx < 0 or scene_idx >= len(scenes):
        return {"error": True, "message": f"无效的分镜索引 {scene_idx}"}
    scene = scenes[scene_idx]

    # 构造图片 prompt：优先 build_scene_prompts，失败则用 description 兜底
    try:
        prompts = build_scene_prompts(scene, character_bible=mv03 if isinstance(mv03, dict) else None)
        image_prompt = prompts.get("image_prompt") or scene.get("prompt_start") or scene.get("description") or ""
    except Exception:
        image_prompt = scene.get("prompt_start") or scene.get("description") or scene.get("visual") or str(scene)

    if not image_prompt:
        return {"error": True, "message": "无法构造图片 prompt"}

    b64, err = generate_image_302(image_prompt)
    if not b64:
        return {"error": True, "message": err or "图片生成失败"}

    # 持久化到磁盘
    local_url = _save_generated_image(b64, sid, scene_idx)
    scene["_image_url"] = local_url
    scene["_image_prompt"] = image_prompt
    return {"url": local_url}


def gen_scene_video(sid: str, scene_idx: int, image_url: str = "") -> Dict[str, Any]:
    """为单个分镜生成视频。下载至本地磁盘，返回可访问的 URL。"""
    s = session_store.require(sid)
    mv04 = s["mv_outputs"].get("MV04")
    mv03 = s["mv_outputs"].get("MV03")
    scenes = _get_scenes_from_mv04(mv04)
    if scene_idx < 0 or scene_idx >= len(scenes):
        return {"error": True, "message": f"无效的分镜索引 {scene_idx}"}
    scene = scenes[scene_idx]

    image_url = image_url or scene.get("_image_url", "")
    if not image_url:
        return {"error": True, "message": "请先生成首帧图片"}

    # 视频 prompt
    try:
        prompts = build_scene_prompts(scene, character_bible=mv03 if isinstance(mv03, dict) else None)
        video_prompt = prompts.get("video_prompt") or scene.get("prompt_video") or scene.get("description") or ""
    except Exception:
        video_prompt = scene.get("prompt_video") or scene.get("description") or ""

    if not video_prompt:
        video_prompt = "电影感长镜头，温暖怀旧的追思氛围，缓慢推进，自然光。"

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
    svc_logger.info("[video] 提交成功 task_id=%s source=%s sid=%s scene=%d", task_id, source, sid, scene_idx)
    return {"task_id": task_id, "source": source, "status": "pending"}


def poll_scene_video(task_id: str, source: str, sid: str, scene_idx: int) -> Dict[str, Any]:
    """轮询视频任务状态。完成后下载到本地并更新 scene。
    返回:
      处理中 → {"status": "processing", "task_id": ...}
      完成   → {"status": "done", "url": "/api/outputs/..."}
      失败   → {"status": "failed", "message": ...}
    """
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
    except Exception:
        pass

    svc_logger.info("[video] 完成 task_id=%s url=%s", task_id, final_url)
    return {"status": "done", "url": final_url}
