import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import gate_manager
from llm_client import call_skill
from skill_loader import load_skill

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

MV_ORDER = ["MV01", "MV02", "MV03", "MV04", "MV05", "MV06"]

MV_FILES = {
    "MV01": "MV01-interview.md",
    "MV02": "MV02-validation.md",
    "MV03": "MV04-bible-lock.md",
    "MV04": "MV03-storyboard.md",
    "MV05": "MV05-avatar-render.md",
    "MV06": "MV06-final-cut.md",
}

mv_state: Dict[str, Dict[str, Any]] = {
    mv_id: {"status": "pending", "duration_sec": None, "error": None}
    for mv_id in MV_ORDER
}


def reset_state() -> None:
    for mv_id in MV_ORDER:
        mv_state[mv_id] = {"status": "pending", "duration_sec": None, "error": None}
    gate_manager.reset_from(MV_ORDER[0])


def get_status() -> Dict[str, Any]:
    return json.loads(json.dumps(mv_state))


def _output_path(mv_id: str) -> Path:
    return OUTPUTS_DIR / f"{mv_id.lower()}.json"


def read_output(mv_id: str) -> Dict[str, Any]:
    path = _output_path(mv_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_output(mv_id: str, payload: Dict[str, Any]) -> None:
    path = _output_path(mv_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_time_range_seconds(time_text: str) -> Optional[int]:
    if not isinstance(time_text, str) or "-" not in time_text:
        return None
    start_text, end_text = [item.strip() for item in time_text.split("-", 1)]

    def to_seconds(value: str) -> Optional[int]:
        parts = value.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return None

    try:
        start_sec = to_seconds(start_text)
        end_sec = to_seconds(end_text)
        if start_sec is None or end_sec is None:
            return None
        return max(0, end_sec - start_sec)
    except Exception:
        return None


def _bucket_duration(seconds: Optional[int]) -> int:
    if seconds is None:
        return 10
    if seconds <= 7:
        return 5
    if seconds <= 12:
        return 10
    return 15


def normalize_storyboard_output(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    scenes = payload.get("scenes")
    if not scenes:
        return payload

    scene_list = []
    if isinstance(scenes, dict):
        for key in sorted(scenes.keys()):
            scene_list.append(scenes[key])
    elif isinstance(scenes, list):
        scene_list = scenes

    for scene in scene_list:
        if not isinstance(scene, dict):
            continue
        duration = _parse_time_range_seconds(scene.get("time", ""))
        bucket = _bucket_duration(duration)
        global_prompt = scene.get("mj_prompt") or scene.get("description") or ""
    scene["duration_sec"] = bucket
    scene["duration_bucket"] = f"{bucket}s"
    scene.setdefault("prompt_global", global_prompt)
    scene.setdefault("prompt_start", f"{global_prompt}, start frame".strip(", "))
    scene.setdefault("prompt_video", f"{global_prompt}, {bucket}s video".strip(", "))

    payload["scenes"] = scenes
    return payload


def save_output(mv_id: str, payload: Dict[str, Any]) -> None:
    if mv_id not in MV_ORDER:
        raise ValueError(f"Unknown mv_id: {mv_id}")
    _write_output(mv_id, payload)


def _load_prompt(mv_id: str) -> str:
    skill_path = SKILLS_DIR / MV_FILES[mv_id]
    return load_skill(str(skill_path))


def get_skill_prompt(mv_id: str) -> str:
    if mv_id not in MV_ORDER:
        raise ValueError(f"Unknown mv_id: {mv_id}")
    return _load_prompt(mv_id)


def _set_state(
    mv_id: str,
    status: str,
    duration: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    mv_state[mv_id]["status"] = status
    mv_state[mv_id]["duration_sec"] = duration
    mv_state[mv_id]["error"] = error


def build_payload(mv_id: str, mv01_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if mv_id == "MV01":
        return mv01_input or {}
    prev_index = MV_ORDER.index(mv_id) - 1
    prev_mv = MV_ORDER[prev_index]
    return read_output(prev_mv)


def run_step(mv_id: str, mv01_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if mv_id not in MV_ORDER:
        raise ValueError(f"Unknown mv_id: {mv_id}")
    if not gate_manager.can_run(mv_id):
        return {"error": True, "skill": mv_id, "message": "上一步未通过审核"}

    gate_manager.set_running(mv_id)
    _set_state(mv_id, "running", None, None)
    start = time.perf_counter()

    try:
        prompt = _load_prompt(mv_id)
        payload = build_payload(mv_id, mv01_input)
        result = call_skill(mv_id, prompt, payload)
    except Exception as exc:  # pragma: no cover
        result = {"error": True, "skill": mv_id, "message": str(exc)}

    duration = time.perf_counter() - start
    if result.get("error"):
        _set_state(mv_id, "error", duration, result.get("message"))
        gate_manager.reject(mv_id, {"error": result.get("message")})
        _write_output(mv_id, {})
        return result

    if mv_id == "MV04":
        result = normalize_storyboard_output(result)
    _write_output(mv_id, result)
    _set_state(mv_id, "awaiting_review", duration, None)
    gate_manager.set_awaiting_review(mv_id)
    return result


def rerun_partial(mv_id: str, scope: Dict[str, Any], prev_output: Dict[str, Any]) -> Dict[str, Any]:
    if mv_id not in MV_ORDER:
        raise ValueError(f"Unknown mv_id: {mv_id}")

    gate_manager.set_running(mv_id)
    _set_state(mv_id, "running", None, None)
    start = time.perf_counter()

    try:
        prompt = _load_prompt(mv_id)
        payload = {"scope": scope, "previous_output": prev_output}
        result = call_skill(mv_id, prompt, payload)
    except Exception as exc:  # pragma: no cover
        result = {"error": True, "skill": mv_id, "message": str(exc)}

    duration = time.perf_counter() - start
    if result.get("error"):
        _set_state(mv_id, "error", duration, result.get("message"))
        gate_manager.reject(mv_id, scope)
        return result

    merged = {**prev_output, **result}
    if mv_id == "MV04":
        merged = normalize_storyboard_output(merged)
    _write_output(mv_id, merged)
    _set_state(mv_id, "awaiting_review", duration, None)
    gate_manager.set_awaiting_review(mv_id)
    return merged


def run_mv04_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    mv_id = "MV04"
    gate_manager.set_running(mv_id)
    _set_state(mv_id, "running", None, None)
    start = time.perf_counter()

    try:
        prompt = _load_prompt(mv_id)
        result = call_skill(mv_id, prompt, payload)
    except Exception as exc:  # pragma: no cover
        result = {"error": True, "skill": mv_id, "message": str(exc)}

    duration = time.perf_counter() - start
    if result.get("error"):
        _set_state(mv_id, "error", duration, result.get("message"))
        gate_manager.reject(mv_id, {"error": result.get("message")})
        _write_output(mv_id, {})
        return result

    if mv_id == "MV04":
        result = normalize_storyboard_output(result)
    _write_output(mv_id, result)
    _set_state(mv_id, "awaiting_review", duration, None)
    gate_manager.set_awaiting_review(mv_id)
    return result


def run_mv03_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    mv_id = "MV03"
    gate_manager.set_running(mv_id)
    _set_state(mv_id, "running", None, None)
    start = time.perf_counter()

    try:
        prompt = _load_prompt(mv_id)
        result = call_skill(mv_id, prompt, payload)
    except Exception as exc:  # pragma: no cover
        result = {"error": True, "skill": mv_id, "message": str(exc)}

    duration = time.perf_counter() - start
    if result.get("error"):
        _set_state(mv_id, "error", duration, result.get("message"))
        gate_manager.reject(mv_id, {"error": result.get("message")})
        _write_output(mv_id, {})
        return result

    _write_output(mv_id, result)
    _set_state(mv_id, "awaiting_review", duration, None)
    gate_manager.set_awaiting_review(mv_id)
    return result
