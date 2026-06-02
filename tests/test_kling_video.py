"""
可灵(Kling)官方API 视频生成接口测试脚本

用法:
  # 交互式测试(提交任务+轮询)
  python -m tests.test_kling_video

  # 只提交不轮询(返回task_id后可手动查)
  python -m tests.test_kling_video --no-poll

  # 只查询已有task
  python -m tests.test_kling_video --query <task_id>

  # 测试JWT生成
  python -m tests.test_kling_video --test-jwt

  # 测试图床上传
  python -m tests.test_kling_video --test-upload

  # 自定义prompt和首帧图
  python -m tests.test_kling_video --prompt "猫咪在草地上奔跑" --image path/to/image.png
"""
import sys
import os
import json
import time
import base64
import argparse
from datetime import datetime

# ─── 加载项目根目录 ──────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(ROOT + "/.env")

# ─── 常量 ─────────────────────────────────────────────────────────────────────
KLING_BASE = "https://api-singapore.klingai.com"
KLING_ID  = os.getenv("KLING_ACCESS_KEY_ID", "")
KLING_SEC = os.getenv("KLING_ACCESS_KEY_SECRET", "")

print(f"[config] KLING_ACCESS_KEY_ID : {KLING_ID[:8]}...{KLING_ID[-4:]}")
print(f"[config] KLING_BASE          : {KLING_BASE}")
print()

# ─── JWT ──────────────────────────────────────────────────────────────────────
def generate_jwt() -> str:
    import jwt
    now = int(time.time())
    payload = {
        "iss": KLING_ID,
        "exp": now + 1800,
        "nbf": now - 5,
    }
    return jwt.encode(payload, KLING_SEC, algorithm="HS256")


# ─── 图床上传 ─────────────────────────────────────────────────────────────────
def upload_to_imgbb(img_bytes: bytes) -> str:
    """上传到 ImgBB，返回公开URL"""
    import requests
    api_key = os.getenv("IMGBB_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 IMGBB_API_KEY")
    r = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "expiration": "3600"},
        files={"image": ("test.png", img_bytes, "image/png")},
        timeout=30,
    )
    data = r.json()
    if data.get("success"):
        return data["data"]["url"]
    raise RuntimeError(f"ImgBB 上传失败: {data}")


# ─── 测试1: JWT ──────────────────────────────────────────────────────────────
def test_jwt():
    print("=" * 60)
    print(" 测试 1: JWT 生成")
    print("=" * 60)
    if not KLING_ID or not KLING_SEC:
        print("[FAIL] 未配置 KLING_ACCESS_KEY_ID / SECRET")
        return None
    try:
        token = generate_jwt()
        print(f"[OK] JWT 生成成功，长度={len(token)}")
        print(f"  Token 前 80 字符: {token[:80]}...")
        return token
    except Exception as e:
        print(f"[FAIL] JWT 生成异常: {e}")
        return None


# ─── 测试2: 图床上传 ─────────────────────────────────────────────────────────
def test_upload(image_path=None):
    print("=" * 60)
    print(" 测试 2: 图片上传图床")
    print("=" * 60)
    try:
        if image_path and os.path.isfile(image_path):
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            print(f"[INFO] 读取本地图片: {image_path} ({len(img_bytes)} bytes)")
        else:
            # 生成一张 16:9 纯色测试图
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (1280, 720), color="#4A90D9")
            draw = ImageDraw.Draw(img)
            draw.rectangle([100, 200, 1180, 520], fill="#FFFFFF")
            draw.text((400, 320), "Kling Video Test", fill="#333333")
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            print("[INFO] 使用生成的纯色测试图 (1280x720)")

        url = upload_to_imgbb(img_bytes)
        print(f"[OK] 上传成功: {url}")
        return url
    except Exception as e:
        print(f"[FAIL] 上传异常: {e}")
        return None


# ─── 测试3: 提交视频任务 ─────────────────────────────────────────────────────
def submit_video(prompt, image_url, poll=False):
    print("=" * 60)
    print(" 测试 3: 提交视频生成任务")
    print("=" * 60)
    print(f"  prompt : {prompt}")
    print(f"  image  : {image_url[:80]}...")

    token = generate_jwt()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "model_name": "kling-v3",
        "prompt": prompt,
        "negative_prompt": "",
        "duration": "5",
        "mode": "std",
        "aspect_ratio": "16:9",
        "sound": "off",
        "image": image_url,
    }

    import requests
    submit_url = f"{KLING_BASE}/v1/videos/image2video"
    print(f"  POST {submit_url}")

    try:
        r = requests.post(submit_url, headers=headers, json=body, timeout=60)
        print(f"  status={r.status_code}")
        resp = r.json()
        print(f"  response: {json.dumps(resp, indent=2, ensure_ascii=False)}")

        code = resp.get("code", -1)
        if code != 0:
            print(f"[FAIL] 提交失败: code={code}, message={resp.get('message')}")
            return None

        task_id = resp.get("data", {}).get("task_id", "")
        if not task_id:
            print("[FAIL] 未返回 task_id")
            return None

        print(f"[OK] task_id={task_id}")
        return task_id
    except Exception as e:
        print(f"[FAIL] 提交异常: {e}")
        return None


# ─── 测试4: 查询任务状态 ─────────────────────────────────────────────────────
def query_task(task_id):
    print("=" * 60)
    print(" 测试 4: 查询任务状态")
    print("=" * 60)
    print(f"  task_id: {task_id}")

    token = generate_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    poll_url = f"{KLING_BASE}/v1/videos/image2video/{task_id}"

    import requests
    try:
        r = requests.get(poll_url, headers=headers, timeout=20)
        resp = r.json()
        print(f"  response: {json.dumps(resp, indent=2, ensure_ascii=False)}")

        task_data = resp.get("data", {})
        status = task_data.get("task_status", "unknown")
        print(f"  status: {status}")

        if status == "succeed":
            videos = task_data.get("task_result", {}).get("videos", [])
            if videos:
                video_url = videos[0].get("url", "")
                print(f"[OK] 视频 URL: {video_url}")
            return task_data
        elif status == "failed":
            msg = task_data.get("task_status_msg", "unknown")
            print(f"[FAIL] 任务失败: {msg}")
            return task_data
        else:
            print(f"[INFO] 当前状态: {status}，继续等待...")
            return task_data
    except Exception as e:
        print(f"[FAIL] 查询异常: {e}")
        return None


# ─── 完整测试流程 ────────────────────────────────────────────────────────────
def run_full_test(prompt, image_path=None, do_poll=True):
    print()
    print("*" * 60)
    print("  Kling API 视频生成 — 完整测试流程")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("*" * 60)
    print()

    # Step 1: JWT
    if not test_jwt():
        return

    # Step 2: 上传图片
    image_url = test_upload(image_path)
    if not image_url:
        print("[ABORT] 图片上传失败，终止测试")
        return

    # Step 3: 提交任务
    task_id = submit_video(prompt, image_url, poll=False)
    if not task_id:
        print("[ABORT] 任务提交失败，终止测试")
        return

    if not do_poll:
        print()
        print("[INFO] --no-poll 模式，跳过轮询。手动查询命令:")
        print(f"  python -m tests.test_kling_video --query {task_id}")
        return

    # Step 4: 轮询等待
    print()
    print("=" * 60)
    print(" 轮询等待任务完成 (每10秒查一次，最多等600秒)")
    print("=" * 60)

    token = generate_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    poll_url = f"{KLING_BASE}/v1/videos/image2video/{task_id}"

    import requests
    max_wait = 600
    interval = 10
    elapsed = 0

    while elapsed < max_wait:
        try:
            # JWT 可能过期了，重新生成
            token = generate_jwt()
            headers = {"Authorization": f"Bearer {token}"}

            r = requests.get(poll_url, headers=headers, timeout=20)
            pd = r.json()
            task_data = pd.get("data", {})
            status = task_data.get("task_status", "")
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] elapsed={elapsed}s | status={status}")

            if status == "succeed":
                videos = task_data.get("task_result", {}).get("videos", [])
                if videos:
                    video_url = videos[0].get("url", "")
                    print(f"\n[SUCCESS] 视频生成成功!")
                    print(f"  URL: {video_url}")
                    print(f"  耗时: {elapsed}s")
                    print(f"\n  完整响应:")
                    print(f"  {json.dumps(pd, indent=2, ensure_ascii=False)}")
                    return True
                print(f"\n[WARN] 任务完成但未返回视频 URL")
                return False

            elif status == "failed":
                msg = task_data.get("task_status_msg", "未知")
                print(f"\n[FAILED] 任务失败")
                print(f"  原因: {msg}")
                print(f"  耗时: {elapsed}s")
                if "risk" in str(msg).lower():
                    print(f"\n[提示] 触发了可灵的内容审核(risk control)，")
                    print(f"       请检查 prompt 是否包含敏感词")
                    print(f"       当前 prompt: {prompt}")
                return False

        except Exception as e:
            print(f"  [WARN] 查询异常(继续等待): {e}")

        elapsed += interval
        if elapsed < max_wait:
            time.sleep(interval)

    print(f"\n[TIMEOUT] 等待超时({max_wait}s)")
    print(f"  可手动查询: python -m tests.test_kling_video --query {task_id}")
    return False


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Kling API 视频生成测试")
    parser.add_argument("--prompt", type=str, default="一只可爱的小猫在花园里慢慢走动，阳光洒在它身上",
                        help="视频生成提示词")
    parser.add_argument("--image", type=str, default=None,
                        help="首帧图片路径(可选，默认生成测试图)")
    parser.add_argument("--no-poll", action="store_true",
                        help="只提交不轮询")
    parser.add_argument("--query", type=str, metavar="TASK_ID",
                        help="查询已有task_id的状态")
    parser.add_argument("--test-jwt", action="store_true",
                        help="仅测试JWT生成")
    parser.add_argument("--test-upload", action="store_true",
                        help="仅测试图床上传")

    args = parser.parse_args()

    if args.query:
        query_task(args.query)
    elif args.test_jwt:
        test_jwt()
    elif args.test_upload:
        test_upload(args.image)
    else:
        run_full_test(args.prompt, args.image, do_poll=not args.no_poll)


if __name__ == "__main__":
    main()
