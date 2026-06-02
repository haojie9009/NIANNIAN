"""Session store key 管理工具。

用法:
    python -m tools.session_tool delete <session_id> key1 [key2 ...]
    python -m tools.session_tool list <session_id>
    python -m tools.session_tool get <session_id> <key>
    python -m tools.session_tool list-ids
"""

import json
import sys
from pathlib import Path
import os

_BASE_DIR = Path(os.environ.get("NIAN_DATA_DIR", str(Path(__file__).resolve().parent.parent / "backend")))
_DATA_DIR = _BASE_DIR / "data" / "sessions"


def _session_path(sid: str) -> Path:
    return _DATA_DIR / f"{sid}.json"


def load_session(sid: str) -> dict:
    p = _session_path(sid)
    if not p.exists():
        raise FileNotFoundError(f"session not found: {sid}, file: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_session(sid: str, data: dict):
    p = _session_path(sid)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(p)


def cmd_list_ids():
    ids = sorted(f.stem for f in _DATA_DIR.glob("*.json") if f.stem)
    print(f"共 {len(ids)} 个 session:")
    for sid in ids:
        data = load_session(sid)
        updated = data.get("updated_at", "")
        print(f"  {sid}  (updated: {updated})")


def cmd_list_keys(sid: str):
    data = load_session(sid)
    print(f"Session {sid} 的顶层 key:")
    for k in sorted(data.keys()):
        v = data[k]
        if isinstance(v, (dict, list)):
            print(f"  {k}: {type(v).__name__} (len={len(v)})")
        elif isinstance(v, str):
            print(f"  {k}: str ({len(v)} chars)")
        else:
            print(f"  {k}: {v!r}")


def cmd_get(sid: str, key: str):
    data = load_session(sid)
    if key not in data:
        print(f"Key '{key}' not found. Available keys: {sorted(data.keys())}")
        return
    print(json.dumps(data[key], ensure_ascii=False, indent=2, default=str))


def cmd_delete(sid: str, keys: list):
    data = load_session(sid)
    deleted = []
    for k in keys:
        if k in data:
            del data[k]
            deleted.append(k)
            print(f"  已删除: {k}")
        else:
            print(f"  不存在: {k}")
    if deleted:
        save_session(sid, data)
        print(f"已删除 {len(deleted)} 个 key 并保存")
    else:
        print("没有删除任何 key")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]

    if cmd == "list-ids":
        cmd_list_ids()
    elif cmd == "list":
        cmd_list_keys(args[1])
    elif cmd == "get":
        cmd_get(args[1], args[2])
    elif cmd == "delete":
        if len(args) < 3:
            print("用法: python -m tools.session_tool delete <session_id> key1 [key2 ...]")
            sys.exit(1)
        cmd_delete(args[1], args[2:])
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)
