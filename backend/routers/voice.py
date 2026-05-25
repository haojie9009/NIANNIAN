# backend/routers/voice.py — 声音工坊：样本管理 + 克隆 + 试听
import os, time, uuid, json, io
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from openai import OpenAI

from core import security, storage

router = APIRouter(prefix="/memorials", tags=["voice"])

# ─── DashScope 区域配置 ──────────────────────────────────────────
# 默认走新加坡（国际）区，与 agent.py / agent_realtime.py 保持一致
# 想切到北京区：设 DASHSCOPE_REGION=cn
def _dashscope_region() -> str:
    return (os.getenv("DASHSCOPE_REGION", "intl") or "intl").lower()

def _http_base() -> str:
    return "https://dashscope-intl.aliyuncs.com/api/v1" if _dashscope_region() == "intl" else "https://dashscope.aliyuncs.com/api/v1"

def _ws_base() -> str:
    return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference" if _dashscope_region() == "intl" else "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

def _apply_region_to_sdk():
    """设置 dashscope SDK 的全局 endpoint（影响 SpeechSynthesizer / VoiceEnrollmentService）"""
    try:
        import dashscope  # type: ignore
        dashscope.base_http_api_url = _http_base()
        dashscope.base_websocket_api_url = _ws_base()
    except Exception:
        pass

# ─── 默认克隆配置 ────────────────────────────────────────────────
DEFAULT_VOICE = {
    "voice_id": "",           # 克隆完成后的 voice_id；空 = 尚未克隆
    "provider": "",           # dashscope / mock
    "status": "idle",         # idle / cloning / ready / failed / mock
    "samples": [],            # 参与克隆的 asset_id 列表
    "params": {
        "speed": 1.0,         # 0.5 ~ 2.0
        "pitch": 0,           # -12 ~ +12 半音
        "volume": 1.0,        # 0 ~ 2
        "emotion": "neutral", # neutral / warm / serious / gentle
        "base_voice": "longxiaochun",  # 未克隆时用的预制音色
    },
    "preview_text": "今天天气真好，我们一起去散步吧。",
    "last_clone_at": "",
    "history": [],            # [{voice_id, created_at, sample_count, note}]
    "error": "",
}

def _voice_path(user_id: str, memorial_id: str) -> Path:
    return storage.memorial_dir(user_id, memorial_id) / "voice.json"

def _get_voice_cfg(user_id: str, memorial_id: str) -> dict:
    p = _voice_path(user_id, memorial_id)
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_VOICE))  # deep copy
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        # 兜底字段
        base = json.loads(json.dumps(DEFAULT_VOICE))
        base.update(d)
        base["params"] = {**DEFAULT_VOICE["params"], **(d.get("params") or {})}
        return base
    except Exception:
        return json.loads(json.dumps(DEFAULT_VOICE))

def _save_voice_cfg(user_id: str, memorial_id: str, cfg: dict):
    p = _voice_path(user_id, memorial_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def _list_audio_assets(user_id: str, memorial_id: str) -> List[dict]:
    """从 assets.json 过滤出音频类素材"""
    try:
        assets = storage.list_assets(user_id, memorial_id)
    except Exception:
        assets = []
    return [a for a in (assets or []) if a.get("kind") == "audio"]


# ─── 路由 ────────────────────────────────────────────────────────
@router.get("/{mid}/voice")
def get_voice(mid: str, user = Depends(security.get_current_user)):
    """获取声音克隆配置 + 可用音频素材列表"""
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "纪念对象不存在")
    cfg = _get_voice_cfg(user["user_id"], mid)
    audios = _list_audio_assets(user["user_id"], mid)
    return {
        "voice": cfg,
        "audio_assets": audios,
        "preset_voices": [
            {"id": "longxiaochun", "name": "龙小淳 · 温柔女声"},
            {"id": "longxiaocheng", "name": "龙小诚 · 沉稳男声"},
            {"id": "longwan", "name": "龙婉 · 柔和女声"},
            {"id": "longcheng", "name": "龙橙 · 阳光男声"},
            {"id": "longhua", "name": "龙华 · 童声"},
        ]
    }


class UpdateVoiceReq(BaseModel):
    params: Optional[dict] = None
    samples: Optional[List[str]] = None  # asset_id 列表
    preview_text: Optional[str] = None

@router.put("/{mid}/voice")
def update_voice(mid: str, req: UpdateVoiceReq, user = Depends(security.get_current_user)):
    """更新克隆参数 / 样本选择 / 试听文案（不触发克隆）"""
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "纪念对象不存在")
    cfg = _get_voice_cfg(user["user_id"], mid)
    if req.params is not None:
        cfg["params"] = {**cfg["params"], **req.params}
    if req.samples is not None:
        cfg["samples"] = req.samples
    if req.preview_text is not None:
        cfg["preview_text"] = req.preview_text
    _save_voice_cfg(user["user_id"], mid, cfg)
    return {"ok": True, "voice": cfg}


class CloneReq(BaseModel):
    sample_ids: List[str]    # 必须从已上传的音频素材里选
    note: str = ""

@router.post("/{mid}/voice/clone")
def clone_voice(mid: str, req: CloneReq, user = Depends(security.get_current_user)):
    """触发声音克隆。
    优先走 Qwen-TTS 路径：把音频 base64 直传给 DashScope（不需要公网 URL）。
    如失败则回落 CosyVoice（需 PUBLIC_BASE_URL）；再失败走 mock。
    """
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "纪念对象不存在")
    audios = _list_audio_assets(user["user_id"], mid)
    valid_ids = {a["asset_id"] for a in audios}
    samples = [s for s in req.sample_ids if s in valid_ids]
    if not samples:
        raise HTTPException(400, "请至少选择一个已上传的音频样本")

    cfg = _get_voice_cfg(user["user_id"], mid)
    cfg["samples"] = samples
    cfg["status"] = "cloning"
    cfg["error"] = ""
    _save_voice_cfg(user["user_id"], mid, cfg)

    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    sample_asset = next(a for a in audios if a["asset_id"] == samples[0])
    sample_path = storage.memorial_dir(user["user_id"], mid) / "assets" / sample_asset.get("stored_name", "")

    voice_id = ""
    provider = ""
    target_model = ""
    err = ""

    if not api_key:
        err = "DASHSCOPE_API_KEY 未配置"
    elif not sample_path.exists():
        err = f"样本文件不存在：{sample_path.name}"
    else:
        # ─── 路径 A：Qwen-TTS（base64 直传，不需要公网 URL） ───
        try:
            import base64, requests
            mime = sample_asset.get("mime", "audio/mpeg") or "audio/mpeg"
            b64 = base64.b64encode(sample_path.read_bytes()).decode()
            data_uri = f"data:{mime};base64,{b64}"
            target_model = "qwen3-tts-vc-2026-01-22"
            url = f"{_http_base()}/services/audio/tts/customization"
            payload = {
                "model": "qwen-voice-enrollment",
                "input": {
                    "action": "create",
                    "target_model": target_model,
                    "preferred_name": f"nian{mid[:6]}",
                    "audio": {"data": data_uri},
                },
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(f"Qwen-TTS HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            voice_id = (data.get("output") or {}).get("voice") or ""
            if not voice_id:
                raise RuntimeError(f"Qwen-TTS 返回无 voice: {str(data)[:300]}")
            provider = "qwen_tts"
            print(f"[voice.clone] qwen_tts OK voice_id={voice_id}")
        except Exception as e_qwen:
            print(f"[voice.clone] qwen_tts failed: {e_qwen}")
            # ─── 路径 B：CosyVoice（公网 URL）兜底 ───
            try:
                import dashscope  # type: ignore
                from dashscope.audio.tts_v2 import VoiceEnrollmentService  # type: ignore
                _apply_region_to_sdk()
                dashscope.api_key = api_key
                public_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
                if not public_base:
                    raise RuntimeError(f"CosyVoice 需要 PUBLIC_BASE_URL；Qwen-TTS 失败原因：{e_qwen}")
                from .uploads import make_asset_sig
                sig = make_asset_sig(mid, sample_asset["asset_id"])
                sample_url = f"{public_base}/api/memorials/{mid}/assets/{sample_asset['asset_id']}/raw?sig={sig}"
                svc = VoiceEnrollmentService()
                target_model = "cosyvoice-v1"
                voice_id = svc.create_voice(target_model=target_model, prefix=f"nian{mid[:6]}", url=sample_url)
                provider = "dashscope"
                print(f"[voice.clone] cosyvoice OK voice_id={voice_id}")
            except Exception as e_cosy:
                err = f"Qwen-TTS 失败:{e_qwen} | CosyVoice 失败:{e_cosy}"
                print(f"[voice.clone] both failed: {err}")

    if not voice_id:
        # mock 兜底，不让前端流程卡死
        voice_id = f"mock_vc_{mid[:6]}_{int(time.time())}"
        provider = "mock"

    cfg["voice_id"] = voice_id
    cfg["provider"] = provider
    cfg["target_model"] = target_model
    cfg["status"] = "ready" if provider in ("qwen_tts", "dashscope") else "mock"
    cfg["last_clone_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    cfg["error"] = err
    cfg["history"] = (cfg.get("history") or [])[-9:] + [{
        "voice_id": voice_id,
        "created_at": cfg["last_clone_at"],
        "sample_count": len(samples),
        "note": req.note,
        "provider": provider,
        "target_model": target_model,
    }]
    _save_voice_cfg(user["user_id"], mid, cfg)
    return {"ok": True, "voice": cfg}


class PreviewReq(BaseModel):
    text: str
    use_clone: bool = True    # True 用克隆音色；False 用预制音色

@router.post("/{mid}/voice/preview")
def preview(mid: str, req: PreviewReq, user = Depends(security.get_current_user)):
    """合成试听音频；返回 audio/mpeg 字节流"""
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "纪念对象不存在")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "请输入试听文本")
    if len(text) > 300:
        text = text[:300]

    cfg = _get_voice_cfg(user["user_id"], mid)
    params = cfg["params"]
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "DASHSCOPE_API_KEY 未配置，无法合成")

    provider = cfg.get("provider", "")
    voice_id = cfg.get("voice_id", "")
    target_model = cfg.get("target_model", "")
    use_clone = req.use_clone and voice_id and provider in ("qwen_tts", "dashscope")

    try:
        # ─── Qwen-TTS 合成 ───
        if use_clone and provider == "qwen_tts":
            import dashscope, base64  # type: ignore
            _apply_region_to_sdk()
            dashscope.api_key = api_key
            model = target_model or "qwen3-tts-vc-2026-01-22"
            resp = dashscope.MultiModalConversation.call(
                model=model, api_key=api_key, text=text, voice=voice_id, stream=False
            )
            # 解析 audio
            audio_bytes = _extract_audio_from_qwen_resp(resp)
            if not audio_bytes:
                print(f"[voice.preview] qwen resp dump: {str(resp)[:500]}")
                raise RuntimeError("Qwen-TTS 返回无音频")
            return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")

        # ─── CosyVoice 合成（克隆或预制） ───
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import SpeechSynthesizer  # type: ignore
        _apply_region_to_sdk()
        dashscope.api_key = api_key
        cosy_voice = voice_id if (use_clone and provider == "dashscope") else params.get("base_voice", "longxiaochun")
        cosy_model = target_model if (use_clone and provider == "dashscope") else "cosyvoice-v1"
        synth = SpeechSynthesizer(model=cosy_model, voice=cosy_voice)
        result = synth.call(text)
        audio_bytes = None
        if isinstance(result, (bytes, bytearray)):
            audio_bytes = bytes(result)
        elif hasattr(result, "get_audio_data"):
            audio_bytes = result.get_audio_data()
        elif hasattr(result, "output"):
            out = getattr(result, "output")
            if isinstance(out, (bytes, bytearray)):
                audio_bytes = bytes(out)
            elif isinstance(out, dict) and "audio" in out:
                audio_bytes = out["audio"]
        if not audio_bytes:
            print(f"[voice.preview] cosy result type={type(result)}, value={str(result)[:200]}")
            raise RuntimeError(f"CosyVoice 返回为空（type={type(result).__name__}）")
        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")
    except ImportError:
        raise HTTPException(500, "服务端未安装 dashscope SDK：pip install dashscope")
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print("[voice.preview] failed:", traceback.format_exc())
        raise HTTPException(500, f"合成失败：{e}")


def _extract_audio_from_qwen_resp(resp) -> bytes | None:
    """从 Qwen-TTS MultiModalConversation 响应里抽取音频字节。
    可能返回：
      - resp.output.audio.data (base64)
      - resp.output.audio.url  (公网 URL，需要再拉一次)
      - resp.output.choices[0].message.content -> [{audio: {data|url}}]
    """
    import base64, requests
    try:
        out = getattr(resp, "output", None) or (resp.get("output") if isinstance(resp, dict) else None)
        if not out:
            return None
        # 路径 1: output.audio
        audio = out.get("audio") if isinstance(out, dict) else None
        if isinstance(audio, dict):
            data = audio.get("data")
            url = audio.get("url")
            if data:
                # data 可能是 base64 或 data URI
                if isinstance(data, str) and data.startswith("data:"):
                    data = data.split(",", 1)[1]
                return base64.b64decode(data)
            if url:
                r = requests.get(url, timeout=30)
                if r.status_code == 200:
                    return r.content
        # 路径 2: choices -> message -> content[].audio
        choices = out.get("choices") if isinstance(out, dict) else None
        if choices and isinstance(choices, list):
            msg = (choices[0] or {}).get("message") or {}
            content = msg.get("content") or []
            for c in content if isinstance(content, list) else []:
                a = c.get("audio") if isinstance(c, dict) else None
                if isinstance(a, dict):
                    if a.get("data"):
                        d = a["data"]
                        if isinstance(d, str) and d.startswith("data:"):
                            d = d.split(",", 1)[1]
                        return base64.b64decode(d)
                    if a.get("url"):
                        r = requests.get(a["url"], timeout=30)
                        if r.status_code == 200:
                            return r.content
    except Exception as e:
        print(f"[voice._extract_audio_from_qwen_resp] {e}")
    return None



@router.delete("/{mid}/voice")
def reset_voice(mid: str, user = Depends(security.get_current_user)):
    """重置：清空当前克隆，回到 idle"""
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "纪念对象不存在")
    cfg = _get_voice_cfg(user["user_id"], mid)
    history = cfg.get("history", [])
    cfg = json.loads(json.dumps(DEFAULT_VOICE))
    cfg["history"] = history  # 保留历史
    _save_voice_cfg(user["user_id"], mid, cfg)
    return {"ok": True, "voice": cfg}


@router.get("/{mid}/voice/diagnose")
def diagnose_voice(mid: str, user = Depends(security.get_current_user)):
    """诊断声音克隆链路：把每个环节单独探一遍，告诉你卡在哪。"""
    if not storage.get_memorial(user["user_id"], mid):
        raise HTTPException(404, "纪念对象不存在")
    result = {"checks": [], "ready_for_clone": False}

    def add(name, ok, detail=""):
        result["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    # 1) dashscope SDK
    try:
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import VoiceEnrollmentService, SpeechSynthesizer  # type: ignore
        add("dashscope SDK 已安装", True, getattr(dashscope, "__version__", "unknown"))
        sdk_ok = True
    except ImportError as e:
        add("dashscope SDK 已安装", False, f"未安装：{e}")
        sdk_ok = False

    # 2) DASHSCOPE_API_KEY
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    add("DASHSCOPE_API_KEY 已配置", bool(api_key), f"长度={len(api_key)}" if api_key else "未设置")

    # 3) PUBLIC_BASE_URL
    public_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    add("PUBLIC_BASE_URL 已配置", bool(public_base), public_base or "未设置")

    # 4) 有音频样本
    audios = _list_audio_assets(user["user_id"], mid)
    add("有可用音频样本", len(audios) > 0, f"共 {len(audios)} 个")

    # 5) 样本公网可拉（拿第一个试探）
    sample_url = ""
    fetch_ok = False
    fetch_detail = "跳过：缺前置条件"
    if audios and public_base:
        a0 = audios[0]
        try:
            from .uploads import make_asset_sig
            sig = make_asset_sig(mid, a0["asset_id"])
            sample_url = f"{public_base}/api/memorials/{mid}/assets/{a0['asset_id']}/raw?sig={sig}"
            import urllib.request
            req = urllib.request.Request(sample_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as resp:
                fetch_ok = (200 <= resp.status < 300)
                fetch_detail = f"HTTP {resp.status} · Content-Type={resp.headers.get('Content-Type','')} · Content-Length={resp.headers.get('Content-Length','')}"
        except Exception as e:
            fetch_detail = f"无法拉取：{type(e).__name__}: {e}"
    add(f"样本可被 DashScope 拉取（HEAD {sample_url[:80]}...）" if sample_url else "样本可被 DashScope 拉取", fetch_ok, fetch_detail)

    result["ready_for_clone"] = sdk_ok and bool(api_key) and bool(public_base) and len(audios) > 0 and fetch_ok
    result["sample_url_example"] = sample_url
    return result


