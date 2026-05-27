"""generate_tts_segments / generate_bgm / _run_mv06_work 单元测试

全部 mock 外部 API（seed_tts, match_bgm, run_pipeline_step, assemble_final_video），
不依赖网络或真实 TTS/BGM 服务。
"""

import io
import struct
import time
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.services.service_manager import (
    generate_tts_segments,
    generate_bgm,
    _run_mv06_work,
    synthesize_tts_text,
    _tts_cache_path,
)
from backend.services.session_store import _SESSIONS, create_session
from backend.services import session_store


# ── helpers ───────────────────────────────────────────────────────────────

def _make_wav_bytes(duration_sec: float = 2.0, sample_rate: int = 24000) -> bytes:
    """生成一段有效的 WAV 字节，用于 mock seed_tts / match_bgm。"""
    buf = io.BytesIO()
    n_frames = int(duration_sec * sample_rate)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def _make_mp3_bytes() -> bytes:
    """生成一个最小的合法 MP3 帧（静音）。"""
    # 一个空 MP3 帧 (MPEG-1 Layer III, 128kbps, 44100Hz)
    # 至少 4 帧，否则 mutagen 可能无法识别
    frame = bytes([
        0xff, 0xfb, 0x90, 0x00,
    ]) + bytes(413)  # padding to ~417 bytes per frame
    return frame * 4


def _fake_scenes_with_narration():
    """构造带旁白文本的分镜列表。"""
    return [
        {"scene_id": "scene_01", "description": "开场", "narration": "你好世界"},
        {"scene_id": "scene_02", "description": "中段", "narration": "这是第二段"},
        {"scene_id": "scene_03", "description": "结尾", "subtitle": "字幕也当旁白"},
    ]


@pytest.fixture(autouse=True)
def _clean_sessions():
    """每次测试前清空内存 session。"""
    _SESSIONS.clear()
    yield
    _SESSIONS.clear()


# ── generate_tts_segments ─────────────────────────────────────────────────

class TestGenerateTtsSegments:

    @patch("backend.services.service_manager.seed_tts")
    def test_basic_synthesis(self, mock_seed):
        """有旁白文本的分镜应逐段合成。"""
        mock_seed.return_value = _make_wav_bytes(2.0)
        scenes = _fake_scenes_with_narration()

        segments = generate_tts_segments("test_sid_001", scenes)

        assert len(segments) == 3
        mock_seed.call_count == 3
        for seg in segments:
            assert "scene_idx" in seg
            assert "audio_url" in seg
            assert "duration_sec" in seg
            assert seg["audio_url"].startswith("/api/outputs/generated/audio/")

    @patch("backend.services.service_manager.seed_tts")
    def test_updates_scene_fields(self, mock_seed):
        """scene 对象应被写入 _tts_audio_url 和 _tts_duration_sec。"""
        mock_seed.return_value = _make_wav_bytes(3.0)
        scenes = [{"narration": "测试文本"}]

        generate_tts_segments("test_sid_002", scenes)

        assert "_tts_audio_url" in scenes[0]
        assert "_tts_duration_sec" in scenes[0]
        assert scenes[0]["_tts_duration_sec"] == pytest.approx(3.0, abs=0.1)

    def test_empty_scenes(self):
        """空分镜列表应返回空。"""
        segments = generate_tts_segments("test_sid_003", [])
        assert segments == []

    def test_no_narration(self):
        """没有旁白/字幕/voiceover 的分镜应跳过。"""
        scenes = [
            {"scene_id": "s1", "description": "只有描述"},
            {"scene_id": "s2"},
        ]
        segments = generate_tts_segments("test_sid_004", scenes)
        assert segments == []

    @patch("backend.services.service_manager.seed_tts")
    def test_skips_failed_synthesis(self, mock_seed):
        """seed_tts 返回 None 的分镜应跳过。"""
        mock_seed.return_value = None
        scenes = [{"narration": "会失败的文本"}]
        segments = generate_tts_segments("test_sid_005", scenes)
        assert segments == []

    @patch("backend.services.service_manager.seed_tts")
    def test_prioritizes_narration_over_subtitle(self, mock_seed):
        """narration 优先级高于 subtitle。"""
        mock_seed.return_value = _make_wav_bytes(1.0)

        captured = {}
        original_seed = __import__("backend.services.llm_client", fromlist=["seed_tts"]).seed_tts
        def _capture(text):
            captured["text"] = text
            return _make_wav_bytes(1.0)

        scenes = [{"narration": "优先旁白", "subtitle": "不应使用"}]
        with patch("backend.services.service_manager.seed_tts", side_effect=_capture):
            generate_tts_segments("test_sid_006", scenes)

        assert captured["text"] == "优先旁白"

    @patch("backend.services.service_manager._tts_cache_path")
    def test_uses_tts_cache(self, mock_cache):
        """已有缓存文件时应直接读取，不调 API。"""
        import tempfile
        wav_data = _make_wav_bytes(2.5)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=Path(__file__).resolve().parent.parent / "backend" / "outputs" / "generated" / "audio") as f:
            f.write(wav_data)
            cached_path = Path(f.name)

        mock_cache.return_value = cached_path
        scenes = [{"narration": "缓存命中"}]

        try:
            segments = generate_tts_segments("test_sid_007", scenes)
            assert len(segments) == 1
            assert segments[0]["duration_sec"] == pytest.approx(2.5, abs=0.2)
        finally:
            cached_path.unlink(missing_ok=True)

    @patch("backend.services.service_manager.seed_tts")
    def test_scene_idx_matches_order(self, mock_seed):
        """scene_idx 应与分镜在列表中的位置一致。"""
        mock_seed.return_value = _make_wav_bytes(1.0)
        scenes = [
            {"narration": "第一段"},
            {"narration": "第二段"},
            {"narration": "第三段"},
        ]
        segments = generate_tts_segments("test_sid_008", scenes)
        assert [s["scene_idx"] for s in segments] == [0, 1, 2]


# ── generate_bgm ─────────────────────────────────────────────────────────

class TestGenerateBgm:

    @patch("backend.services.service_manager.match_bgm")
    def test_returns_bgm_result(self, mock_match):
        """应返回 match_bgm 的结果。"""
        sid = create_session()
        fake = {
            "emotion": "warm_nostalgia",
            "bgm_url": "/api/outputs/generated/audio/test_bgm.mp3",
            "duration_sec": 120.0,
            "bgm_program": {"base_volume": 0.3},
        }
        mock_match.return_value = fake

        result = generate_bgm(sid)

        assert result == fake

    @patch("backend.services.service_manager.match_bgm")
    def test_persists_to_session(self, mock_match):
        """BGM 结果应写入 session['audio']['bgm']。"""
        sid = create_session()
        fake = {
            "emotion": "peaceful_serene",
            "bgm_url": "/api/outputs/generated/audio/test_bgm.mp3",
            "duration_sec": 60.0,
        }
        mock_match.return_value = fake

        generate_bgm(sid)

        s = session_store.require(sid)
        assert s["audio"]["bgm"] == fake

    @patch("backend.services.service_manager.match_bgm")
    def test_missing_session_raises(self, mock_match):
        """不存在的 session 应抛 KeyError。"""
        with pytest.raises(KeyError, match="session not found"):
            generate_bgm("nonexistent_sid")

    @patch("backend.services.service_manager.match_bgm")
    def test_creates_audio_key(self, mock_match):
        """session 中无 audio 键时应自动创建。"""
        sid = create_session()
        # 手动清空 audio
        s = session_store.require(sid)
        s["audio"] = {}
        session_store.update(sid)

        fake = {"emotion": "solemn_respect", "bgm_url": None, "duration_sec": None}
        mock_match.return_value = fake

        generate_bgm(sid)

        s = session_store.require(sid)
        assert "audio" in s
        assert s["audio"]["bgm"] == fake


# ── _run_mv06_work ──────────────────────────────────────────────────

class TestRunMv06WithAudio:

    def _prepare_session(self, sid, scenes=None):
        """准备一个已跑完 MV04 的 session。"""
        s = session_store.require(sid)
        s["mv_outputs"]["MV04"] = {"scenes": scenes or [
            {
                "scene_id": "scene_01",
                "description": "开场",
                "narration": "你好",
                "_image_url": "/api/outputs/generated/images/test.png",
            }
        ]}
        s["form_data"]["deceased_name"] = "测试"
        session_store.update(sid)
        return s

    @patch("backend.services.service_manager.assemble_final_video")
    @patch("backend.services.service_manager.run_pipeline_step")
    @patch("backend.services.service_manager.match_bgm")
    @patch("backend.services.service_manager.seed_tts")
    def test_full_pipeline_flow(self, mock_seed, mock_bgm, mock_mv06, mock_assemble):
        """完整流程：批准 MV05 → TTS → BGM → MV06 → 视频拼接。"""
        sid = create_session()
        self._prepare_session(sid)

        mock_seed.return_value = _make_wav_bytes(2.0)
        mock_bgm.return_value = {
            "emotion": "warm_nostalgia",
            "bgm_url": "/api/outputs/generated/audio/bg.mp3",
            "duration_sec": 120.0,
            "bgm_program": {},
        }
        mock_mv06.return_value = {"ok": True, "step": "MV06", "result": {"timeline": []}}
        mock_assemble.return_value = {
            "ok": True, "video_url": "/api/outputs/final_cuts/final.mp4",
            "duration_sec": 10.0, "scenes_count": 1,
        }

        result = _run_mv06_work(sid)

        s = session_store.require(sid)
        mr = s["mv06_result"]
        assert mr["ok"] is True
        assert mr["final_video_url"] == "/api/outputs/final_cuts/final.mp4"
        assert mr["video_duration_sec"] == 10.0
        assert "audio" in mr
        assert len(mr["audio"]["tts_segments"]) >= 1
        assert mr["audio"]["bgm"]["emotion"] == "warm_nostalgia"

    @patch("backend.services.service_manager.assemble_final_video")
    @patch("backend.services.service_manager.run_pipeline_step")
    @patch("backend.services.service_manager.match_bgm")
    @patch("backend.services.service_manager.seed_tts")
    def test_mv05_gate_auto_approved(self, mock_seed, mock_bgm, mock_mv06, mock_assemble):
        """MV05 闸门应被自动批准。"""
        sid = create_session()
        self._prepare_session(sid)

        mock_seed.return_value = _make_wav_bytes(1.0)
        mock_bgm.return_value = {"emotion": "warm", "bgm_url": None, "duration_sec": None}
        mock_mv06.return_value = {"ok": True, "result": {}}
        mock_assemble.return_value = {"ok": False, "error": "no assets"}

        _run_mv06_work(sid)

        s = session_store.require(sid)
        assert s["pipeline_state"]["MV05"]["status"] == "approved"

    @patch("backend.services.service_manager.assemble_final_video")
    @patch("backend.services.service_manager.run_pipeline_step")
    @patch("backend.services.service_manager.match_bgm")
    @patch("backend.services.service_manager.seed_tts")
    def test_saves_tts_to_session(self, mock_seed, mock_bgm, mock_mv06, mock_assemble):
        """TTS 段落应保存到 session['audio']['tts_segments']。"""
        sid = create_session()
        self._prepare_session(sid, scenes=[
            {"narration": "第一段", "scene_id": "s1"},
            {"narration": "第二段", "scene_id": "s2"},
        ])

        mock_seed.return_value = _make_wav_bytes(1.5)
        mock_bgm.return_value = {"emotion": "warm", "bgm_url": None, "duration_sec": None}
        mock_mv06.return_value = {"ok": True, "result": {}}
        mock_assemble.return_value = {"ok": False, "error": "skip"}

        _run_mv06_work(sid)

        s = session_store.require(sid)
        tts = s["audio"]["tts_segments"]
        assert len(tts) == 2
        assert tts[0]["scene_idx"] == 0
        assert tts[1]["scene_idx"] == 1

    @patch("backend.services.service_manager.assemble_final_video")
    @patch("backend.services.service_manager.run_pipeline_step")
    @patch("backend.services.service_manager.match_bgm")
    @patch("backend.services.service_manager.seed_tts")
    def test_video_error_attached_to_result(self, mock_seed, mock_bgm, mock_mv06, mock_assemble):
        """视频拼接失败时，error 应附加到返回结果。"""
        sid = create_session()
        self._prepare_session(sid)

        mock_seed.return_value = _make_wav_bytes(1.0)
        mock_bgm.return_value = {"emotion": "warm", "bgm_url": None, "duration_sec": None}
        mock_mv06.return_value = {"ok": True, "result": {}}
        mock_assemble.return_value = {"ok": False, "error": "no usable scenes"}

        _run_mv06_work(sid)

        s = session_store.require(sid)
        mr = s["mv06_result"]
        assert mr["video_error"] == "no usable scenes"
        assert "final_video_url" not in mr

    @patch("backend.services.service_manager.assemble_final_video")
    @patch("backend.services.service_manager.run_pipeline_step")
    @patch("backend.services.service_manager.match_bgm")
    @patch("backend.services.service_manager.seed_tts")
    def test_no_narration_no_tts(self, mock_seed, mock_bgm, mock_mv06, mock_assemble):
        """分镜没有旁白时，tts_segments 应为空。"""
        sid = create_session()
        self._prepare_session(sid, scenes=[
            {"scene_id": "s1", "description": "纯画面"},
        ])

        mock_bgm.return_value = {"emotion": "warm", "bgm_url": None, "duration_sec": None}
        mock_mv06.return_value = {"ok": True, "result": {}}
        mock_assemble.return_value = {"ok": False, "error": "skip"}

        _run_mv06_work(sid)

        s = session_store.require(sid)
        assert s["audio"]["tts_segments"] == []
        # seed_tts 不应被调用
        mock_seed.assert_not_called()

    @patch("backend.services.service_manager.assemble_final_video")
    @patch("backend.services.service_manager.run_pipeline_step")
    @patch("backend.services.service_manager.match_bgm")
    @patch("backend.services.service_manager.seed_tts")
    def test_bgm_persisted_to_session(self, mock_seed, mock_bgm, mock_mv06, mock_assemble):
        """BGM 结果应持久化到 session。"""
        sid = create_session()
        self._prepare_session(sid)

        mock_seed.return_value = _make_wav_bytes(1.0)
        bgm_data = {
            "emotion": "gentle_sorrow",
            "bgm_url": "/api/outputs/generated/audio/gs.mp3",
            "duration_sec": 90.0,
            "bgm_program": {"base_volume": 0.25},
        }
        mock_bgm.return_value = bgm_data
        mock_mv06.return_value = {"ok": True, "result": {}}
        mock_assemble.return_value = {"ok": False, "error": "skip"}

        _run_mv06_work(sid)

        s = session_store.require(sid)
        assert s["audio"]["bgm"]["emotion"] == "gentle_sorrow"
        assert s["audio"]["bgm"]["bgm_url"] == bgm_data["bgm_url"]
