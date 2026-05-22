# video_editor.py — 视频剪辑合成模块
"""
将多个视频片段（HTTPS URL）下载后按顺序拼接，输出为单个 MP4 文件。
依赖：moviepy 2.x、requests
"""
from __future__ import annotations
import os
import tempfile
import time
from pathlib import Path
from typing import List, Callable, Optional

import requests


# ── 输出目录 ───────────────────────────────────────────────────────────────────
_OUTPUT_DIR = Path(__file__).parent / "outputs" / "final_cuts"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def download_video(url: str, dest: str, progress_cb: Optional[Callable[[int, int], None]] = None) -> str:
    """
    从 URL 下载视频到 dest 路径。
    progress_cb(downloaded_bytes, total_bytes) 可选进度回调。
    返回 dest 路径。
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=60, stream=True)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 64):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total:
                    progress_cb(downloaded, total)
    return dest


def concat_clips(
    video_urls: List[str],
    output_filename: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """
    下载 video_urls 中的所有视频，按顺序拼接，返回输出 MP4 的绝对路径。

    参数
    ----
    video_urls      : 视频 HTTPS URL 列表，顺序即最终顺序
    output_filename : 输出文件名（不含路径），默认按时间戳命名
    progress_cb     : 进度文本回调，例如 lambda msg: st.write(msg)

    返回
    ----
    输出 MP4 文件的绝对路径
    """
    from moviepy import VideoFileClip, concatenate_videoclips  # moviepy 2.x

    if not video_urls:
        raise ValueError("video_urls 不能为空")

    if output_filename is None:
        output_filename = f"final_cut_{int(time.time())}.mp4"

    output_path = str(_OUTPUT_DIR / output_filename)
    tmp_dir = tempfile.mkdtemp(prefix="niancut_")

    clips = []
    try:
        for i, url in enumerate(video_urls):
            if progress_cb:
                progress_cb(f"下载第 {i+1}/{len(video_urls)} 个片段…")
            ext = (url.split("?")[0].rsplit(".", 1)[-1] or "mp4").lower()
            if ext not in ("mp4", "mov", "webm", "mkv"):
                ext = "mp4"
            tmp_path = os.path.join(tmp_dir, f"clip_{i:03d}.{ext}")
            download_video(url, tmp_path)
            clip = VideoFileClip(tmp_path)
            clips.append(clip)

        if progress_cb:
            progress_cb(f"正在合成 {len(clips)} 个片段…")

        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=os.path.join(tmp_dir, "tmp_audio.m4a"),
            remove_temp=True,
            logger=None,      # 抑制 moviepy 进度条输出
        )
        final.close()

    finally:
        for c in clips:
            try:
                c.close()
            except Exception:
                pass

    return output_path
