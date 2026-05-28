"""Session 调试工具 —— 通过 admin API 查看 / 操作 session_store 内容。

用法:
    python scripts/session_tool.py code                          # 主人访问码登录 (默认码: 666666)
    python scripts/session_tool.py login                         # 邮箱密码登录
    python scripts/session_tool.py list                          # 列出所有 session 摘要
    python scripts/session_tool.py detail <session_id>           # 查看完整内容
    python scripts/session_tool.py unlock <session_id>           # 解锁 gate
    python scripts/session_tool.py delete <session_id>           # 删除 session
"""

import sys, json, os
from pathlib import Path

import requests

BASE = os.getenv("NIAN_API", "http://localhost:8000/api")
TOKEN_FILE = Path(__file__).parent / ".nian_token"


def _token():
    """从缓存文件读取 token，不存在则返回 None。"""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def _save_token(tok):
    TOKEN_FILE.write_text(tok)


def _headers():
    tok = _token()
    if not tok:
        print("未登录，请先执行: python scripts/session_tool.py login")
        sys.exit(1)
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _pp(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ---- 命令实现 ----


def cmd_login():
    email = input("邮箱: ").strip()
    password = input("密码: ").strip()
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        print(f"登录失败: {r.text}")
        sys.exit(1)
    body = r.json()
    _save_token(body["token"])
    print("登录成功，token 已缓存。")
    _pp(body["user"])


def cmd_code():
    """使用主人访问码登录。"""
    code = input("主人访问码: ").strip()
    r = requests.post(f"{BASE}/auth/code", json={"code": code})
    if r.status_code != 200:
        print(f"登录失败: {r.text}")
        sys.exit(1)
    body = r.json()
    _save_token(body["token"])
    print("主人登录成功，token 已缓存。")
    _pp(body["user"])


def cmd_list():
    r = requests.get(f"{BASE}/admin/sessions", headers=_headers())
    if r.status_code != 200:
        print(f"请求失败: {r.text}")
        sys.exit(1)
    data = r.json()
    _pp(data)
    # 简表
    for s in data.get("sessions", []):
        sid = s["session_id"][:12] + "..."
        name = s.get("form_name") or "(未填)"
        chats = s.get("chat_history_len", 0)
        gates = s.get("pipeline_state", {})
        mv = ", ".join(gates) if gates else "无"
        print(f"  {sid}  name={name}  chats={chats}  pipeline=[{mv}]")
    print(f"\n共 {data['count']} 个活跃 session")


def cmd_detail(sid):
    r = requests.get(f"{BASE}/admin/sessions/{sid}", headers=_headers())
    if r.status_code != 200:
        print(f"请求失败: {r.text}")
        sys.exit(1)
    _pp(r.json())


def cmd_unlock(sid):
    r = requests.post(f"{BASE}/admin/sessions/{sid}/unlock-gate", headers=_headers())
    if r.status_code != 200:
        print(f"请求失败: {r.text}")
        sys.exit(1)
    print("Gate 已解锁:")
    _pp(r.json())


def cmd_delete(sid):
    confirm = input(f"确认删除 session {sid}? (y/n): ").strip().lower()
    if confirm != "y":
        print("已取消。")
        return
    r = requests.delete(f"{BASE}/admin/sessions/{sid}", headers=_headers())
    if r.status_code != 200:
        print(f"请求失败: {r.text}")
        sys.exit(1)
    print(f"已删除: {r.json()}")


# ---- 入口 ----

COMMANDS = {
    "login": (cmd_login, 0, "邮箱密码登录"),
    "code": (cmd_code, 0, "主人访问码登录"),
    "list": (cmd_list, 0, "列出所有活跃 session"),
    "detail": (cmd_detail, 1, "查看单个 session 完整内容"),
    "unlock": (cmd_unlock, 1, "解锁 session 的所有 gate"),
    "delete": (cmd_delete, 1, "删除一个 session"),
}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("可用命令:")
        for name, (_, nargs, desc) in COMMANDS.items():
            args_str = " ".join(f"<arg{i + 1}>" for i in range(nargs))
            print(f"  {name} {args_str}  — {desc}")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd not in COMMANDS:
        print(f"未知命令: {cmd}")
        sys.exit(1)

    fn, nargs, _ = COMMANDS[cmd]
    if len(sys.argv) - 2 < nargs:
        print(f"命令 '{cmd}' 需要 {nargs} 个参数")
        sys.exit(1)

    if nargs == 0:
        fn()
    elif nargs == 1:
        fn(sys.argv[2])


if __name__ == "__main__":
    main()
