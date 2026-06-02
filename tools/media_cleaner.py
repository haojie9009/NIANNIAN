"""
交互式媒体清理脚本：按 session → 媒体 key → 选择删除
- 列出所有 session，带上名称和日期
- 只列出包含视频/音频/图片 URL 的 key，显示完整父路径（如 audio.bgm.bgm_url）
- 选中删除后，同时清理 session_store JSON 和磁盘文件

用法:
    python -m tools.media_cleaner          # 交互式选择 session
    python -m tools.media_cleaner <sid>     # 指定 session
"""
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
SESSIONS_DIR = PROJECT_DIR / "backend" / "data" / "sessions"
BACKEND_DIR = PROJECT_DIR / "backend"

MEDIA_PREFIXES = (
    "/api/outputs/generated/images/",
    "/api/outputs/generated/videos/",
    "/api/outputs/generated/audio/",
    "/api/outputs/final_cuts/",
)


def is_media_url(v: str) -> bool:
    return isinstance(v, str) and any(v.startswith(p) for p in MEDIA_PREFIXES)


def media_type_label(url: str) -> str:
    if "/images/" in url:
        return "图片"
    if "/videos/" in url or "/final_cuts/" in url:
        return "视频"
    return "音频"


def url_to_local(url: str) -> str | None:
    """将 /api/outputs/... 映射到 backend/outputs/..."""
    if url.startswith("/api/outputs/"):
        return str(BACKEND_DIR / "outputs" / url[len("/api/outputs/"):])
    return None


def scan_media(obj, prefix=""):
    """递归扫描，返回 [(full_path, url, type_label), ...]
    full_path 格式：audio.bgm.bgm_url、mv_outputs.MV04.scenes.scene_01._video_url
    """
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            cur = f"{prefix}.{k}" if prefix else k
            if is_media_url(v):
                results.append((cur, v, media_type_label(v)))
            else:
                results.extend(scan_media(v, cur))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            cur = f"{prefix}[{i}]"
            if is_media_url(v):
                results.append((cur, v, media_type_label(v)))
            else:
                results.extend(scan_media(v, cur))
    return results


def delete_by_path(data, dotpath: str):
    """根据点路径删除 dict/list 中的值"""
    parts = []
    for seg in dotpath.split("."):
        if "[" in seg:
            name, idx_str = seg.split("[")
            idx = int(idx_str.rstrip("]"))
            if name:
                parts.append(name)
            parts.append(idx)
        else:
            parts.append(seg)

    cur = data
    for p in parts[:-1]:
        cur = cur[p]
    last = parts[-1]
    if isinstance(last, int):
        cur[last] = None
    else:
        del cur[last]


def list_sessions():
    """列出磁盘上所有 session，带上基本信息"""
    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sid = data.get("session_id", f.stem)
            name = data.get("form_data", {}).get("deceased_name", "未知")
            updated = data.get("updated_at", 0)
            sessions.append((sid, name, updated, str(f)))
        except Exception:
            sessions.append((f.stem, "解析失败", 0, str(f)))
    return sessions


def group_by_parent(entries):
    """按二级父节点分组展示，如 audio.bgm / audio.tts_segments / mv_outputs.MV04 ..."""
    groups = {}
    for path, url, typ in entries:
        # 取前两段作为分组：audio.bgm 或 mv_outputs.MV04
        parts = path.split(".")
        if len(parts) >= 2:
            group_key = f"{parts[0]}.{parts[1]}"
        else:
            group_key = parts[0]
        groups.setdefault(group_key, []).append((path, url, typ))
    return groups


def main():
    # 1) 选择 session
    if len(sys.argv) > 1:
        sid = sys.argv[1]
    else:
        sessions = list_sessions()
        if not sessions:
            print("没有找到 session 文件")
            return
        print("\n=== 可用 session ===\n")
        for i, (sid, name, updated, _) in enumerate(sessions):
            from datetime import datetime
            dt = datetime.fromtimestamp(updated).strftime("%Y-%m-%d %H:%M")
            print(f"  [{i}] {sid[:12]}...  逝者: {name}  更新: {dt}")
        sel = input("\n选择序号: ").strip()
        sid, _, _, _ = sessions[int(sel)]

    # 2) 加载 session
    json_file = SESSIONS_DIR / f"{sid}.json"
    if not json_file.exists():
        print(f"文件不存在: {json_file}")
        return
    data = json.loads(json_file.read_text(encoding="utf-8"))

    # 3) 扫描媒体 key
    entries = scan_media(data)
    if not entries:
        print(f"\nSession {sid} 中没有找到媒体资源。")
        return

    # 4) 按父节点分组展示
    groups = group_by_parent(entries)
    print(f"\n=== Session: {sid} ===")
    print(f"共 {len(entries)} 个媒体 key，{len(groups)} 个分组:\n")

    idx = 0
    idx_map = {}  # global_index -> (path, url, typ)
    for group_name, items in groups.items():
        print(f"  ── {group_name} ({len(items)} 个) ──")
        for path, url, typ in items:
            file_status = ""
            local = url_to_local(url)
            if local and os.path.exists(local):
                size = os.path.getsize(local)
                if size > 1024 * 1024:
                    file_status = f"  [{size // 1024 // 1024}MB]"
                else:
                    file_status = f"  [{size // 1024}KB]"
            else:
                file_status = "  [磁盘缺失]"

            print(f"    [{idx:>2}] {typ}  {path}{file_status}")
            idx_map[idx] = (path, url, typ)
            idx += 1
        print()

    # 5) 选择删除
    sel = input("输入要删除的序号（逗号分隔，a=全部，q=退出）: ").strip()
    if sel.lower() == "q":
        return
    if sel.lower() == "a":
        targets = list(range(len(idx_map)))
    else:
        targets = sorted(set(int(x.strip()) for x in sel.split(",") if x.strip()))

    # 6) 收集本地文件
    files_to_delete = set()
    for i in targets:
        _, url, _ = idx_map[i]
        local = url_to_local(url)
        if local:
            files_to_delete.add(local)

    existing = [f for f in files_to_delete if os.path.exists(f)]
    missing = [f for f in files_to_delete if not os.path.exists(f)]

    print(f"\n即将删除 {len(targets)} 个 key，以及 {len(existing)} 个本地文件：")
    for f in existing:
        size = os.path.getsize(f)
        sz = f"{size // 1024 // 1024}MB" if size > 1024 * 1024 else f"{size // 1024}KB"
        print(f"  [存在 {sz}] {f}")
    for f in missing:
        print(f"  [缺失] {f}")

    confirm = input(f"\n确认删除？(y/N): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return

    # 7) 删除 session 中的 key
    for i in targets:
        path, url, typ = idx_map[i]
        try:
            delete_by_path(data, path)
            print(f"  已删除 key: {path}")
        except Exception as e:
            print(f"  删除 key 失败: {path} — {e}")

    # 8) 写回 session_store
    tmp = json_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(json_file)
    print(f"\nSession 已保存: {json_file}")

    # 9) 删除本地文件
    deleted, failed = 0, 0
    for f in existing:
        try:
            os.remove(f)
            deleted += 1
            print(f"  已删除文件: {f}")
        except Exception as e:
            failed += 1
            print(f"  删除文件失败: {f} — {e}")

    print(f"\n完成: 删除 {len(targets)} 个 key, {deleted} 个文件, {failed} 个失败")


if __name__ == "__main__":
    main()
