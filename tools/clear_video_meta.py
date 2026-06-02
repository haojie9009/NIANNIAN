"""
清理 session 中的视频任务元数据（_video_task_id, _video_status 等）
这样前端再次点击"生成视频"时会重新调用 Kling API。

用法:
    python -m tools.clear_video_meta          # 交互式选择 session
    python -m tools.clear_video_meta <sid>     # 直接指定 session
"""
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_DIR / "backend" / "data" / "sessions"

META_KEYS = [
    "_video_task_id",
    "_video_task_source",
    "_video_status",
    "_video_url",
    "_video_prompt_version",
]


def load_session(sid):
    f = SESSIONS_DIR / f"{sid}.json"
    if not f.exists():
        print(f"文件不存在: {f}")
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    return data, str(f)


def count_scenes(data):
    """统计场景数"""
    try:
        scenes = data.get("mv_outputs", {}).get("MV04", {}).get("scenes", {})
        if isinstance(scenes, dict):
            return len(scenes)
        return len(scenes)
    except (AttributeError, TypeError):
        return 0


def clear_scene_meta(data):
    """遍历所有场景，删除视频元数据"""
    deleted = 0
    scenes = data.get("mv_outputs", {}).get("MV04", {}).get("scenes", {})
    if isinstance(scenes, dict):
        scenes_values = scenes.values()
    elif isinstance(scenes, list):
        scenes_values = scenes
    else:
        return 0
    for scene in scenes_values:
        if not isinstance(scene, dict):
            continue
        for key in META_KEYS:
            if key in scene:
                del scene[key]
                deleted += 1
    return deleted


def list_sessions():
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sid = data.get("session_id", f.stem)
            name = data.get("form_data", {}).get("deceased_name", "未知")
            updated = data.get("updated_at", 0)
            sessions.append((sid, name, updated))
        except Exception:
            sessions.append((f.stem, "解析失败", 0))
    return sessions


def main():
    if len(sys.argv) > 1:
        sid = sys.argv[1]
    else:
        sessions = list_sessions()
        if not sessions:
            print("没有找到 session")
            return
        print("\n=== 可用 session ===\n")
        for i, (sid, name, updated) in enumerate(sessions):
            from datetime import datetime
            dt = datetime.fromtimestamp(updated).strftime("%Y-%m-%d %H:%M")
            scenes = 0
            try:
                f = SESSIONS_DIR / f"{sid}.json"
                d = json.loads(f.read_text(encoding="utf-8"))
                scenes = count_scenes(d)
            except Exception:
                pass
            print(f"  [{i}] {sid[:12]}...  逝者: {name}  分镜: {scenes}  更新: {dt}")
        sel = input("\n选择序号: ").strip()
        sid, _, _ = sessions[int(sel)]

    result = load_session(sid)
    if result is None:
        return
    data, fpath = result

    deleted = clear_scene_meta(data)
    if deleted == 0:
        print(f"\nSession {sid[:12]}... 中没有找到视频元数据 key")
        return

    print(f"\n已删除 {deleted} 个视频元数据 key:")
    print(f"  session: {sid[:12]}...")
    print(f"  文件: {fpath}")

    tmp = Path(fpath).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(Path(fpath))
    print(f"\nSession 已保存。重启服务器后再次点击'生成视频'即可。")


if __name__ == "__main__":
    main()
