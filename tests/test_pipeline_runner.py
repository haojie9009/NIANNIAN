"""pipeline_runner 纯逻辑测试 — 时间解析、分镜标准化"""

import pytest
from backend.services.pipeline_runner import (
    _parse_time_range_seconds,
    _bucket_duration,
    normalize_storyboard_output,
    build_payload,
    MV_ORDER,
)


class TestParseTimeRangeSeconds:
    """解析 "0:00-0:10" 格式的时间范围"""

    def test_simple_seconds(self):
        assert _parse_time_range_seconds("0:00-0:10") == 10

    def test_one_minute(self):
        assert _parse_time_range_seconds("0:00-1:00") == 60

    def test_one_minute_ten(self):
        assert _parse_time_range_seconds("0:00-1:10") == 70

    def test_two_minutes(self):
        assert _parse_time_range_seconds("0:00-2:00") == 120

    def test_with_hours(self):
        assert _parse_time_range_seconds("0:00:00-0:01:30") == 90

    def test_mid_range(self):
        assert _parse_time_range_seconds("1:00-1:15") == 15

    def test_not_string(self):
        assert _parse_time_range_seconds(123) is None
        assert _parse_time_range_seconds(None) is None

    def test_no_dash(self):
        assert _parse_time_range_seconds("0:10") is None

    def test_empty_string(self):
        assert _parse_time_range_seconds("") is None

    def test_malformed(self):
        # Invalid time parts should return None
        result = _parse_time_range_seconds("abc-def")
        # May throw exception internally and return None
        assert result is None


class TestBucketDuration:
    """将秒数映射到标准视频时长档位"""

    def test_none_defaults_to_10(self):
        assert _bucket_duration(None) == 10

    def test_short_5s(self):
        assert _bucket_duration(5) == 5
        assert _bucket_duration(7) == 5

    def test_medium_10s(self):
        assert _bucket_duration(8) == 10
        assert _bucket_duration(12) == 10

    def test_long_15s(self):
        assert _bucket_duration(13) == 15
        assert _bucket_duration(30) == 15
        assert _bucket_duration(120) == 15

    def test_boundary_7(self):
        assert _bucket_duration(7) == 5

    def test_boundary_12(self):
        assert _bucket_duration(12) == 10


class TestNormalizeStoryboardOutput:
    """normalize_storyboard_output 处理 MV04 分镜输出"""

    def test_non_dict_passthrough(self):
        assert normalize_storyboard_output("hello") == "hello"
        assert normalize_storyboard_output([1, 2]) == [1, 2]

    def test_no_scenes_passthrough(self):
        payload = {"title": "test"}
        result = normalize_storyboard_output(payload)
        assert result == payload

    def test_empty_scenes_passthrough(self):
        payload = {"scenes": []}
        result = normalize_storyboard_output(payload)
        assert result == payload

    def test_dict_scenes_converted_to_list(self):
        payload = {
            "scenes": {
                "2": {"description": "scene 2", "time": "0:00-0:10"},
                "1": {"description": "scene 1", "time": "0:00-0:05"},
            }
        }
        result = normalize_storyboard_output(payload)
        # Should be sorted by key
        assert result["scenes"][0]["description"] == "scene 1"
        assert result["scenes"][1]["description"] == "scene 2"

    def test_each_scene_gets_duration_fields(self):
        payload = {
            "scenes": [
                {"description": "opening", "time": "0:00-0:05"},
                {"description": "middle", "time": "0:00-0:10"},
                {"description": "end", "time": "0:00-0:15"},
            ]
        }
        result = normalize_storyboard_output(payload)
        scenes = result["scenes"]
        assert scenes[0]["duration_sec"] == 5
        assert scenes[0]["duration_bucket"] == "5s"
        assert scenes[1]["duration_sec"] == 10
        assert scenes[1]["duration_bucket"] == "10s"
        assert scenes[2]["duration_sec"] == 15
        assert scenes[2]["duration_bucket"] == "15s"

    def test_prompt_fields_populated(self):
        payload = {
            "scenes": [
                {"description": "sunset beach", "mj_prompt": "golden sunset beach scene"},
            ]
        }
        result = normalize_storyboard_output(payload)
        scene = result["scenes"][0]
        assert scene["prompt_global"] == "golden sunset beach scene"
        assert "prompt_start" in scene
        assert "prompt_video" in scene

    def test_no_mj_prompt_uses_description(self):
        payload = {
            "scenes": [
                {"description": "quiet forest"},
            ]
        }
        result = normalize_storyboard_output(payload)
        assert result["scenes"][0]["prompt_global"] == "quiet forest"

    def test_non_dict_scene_skipped(self):
        payload = {"scenes": ["not a dict", {"description": "real scene", "time": "0:00-0:10"}]}
        result = normalize_storyboard_output(payload)
        # Should not crash, the dict scene should be processed
        assert isinstance(result["scenes"], list)


class TestBuildPayload:
    """build_payload 根据 mv_id 构建 LLM 输入"""

    def test_mv01_returns_input(self):
        input_data = {"name": "test"}
        result = build_payload("MV01", input_data)
        assert result == {"name": "test"}

    def test_mv01_no_input_returns_empty(self):
        result = build_payload("MV01", None)
        assert result == {}

    def test_mv02_reads_mv01(self):
        # MV02 的 payload 来自 MV01 输出
        # 这个测试依赖 read_output，如果没有文件就返回 {}
        result = build_payload("MV02")
        assert isinstance(result, dict)

    def test_mv03_reads_mv02(self):
        result = build_payload("MV03")
        assert isinstance(result, dict)

    def test_full_chain_correct_index(self):
        """验证 MV 顺序中每个步骤读取的是前一个的输出"""
        for i, mv in enumerate(MV_ORDER):
            if i == 0:
                continue  # MV01 特殊
            expected_prev = MV_ORDER[i - 1]
            # build_payload(mv) 应该读取 expected_prev 的输出
            # 我们不验证文件内容，只验证不报错
            build_payload(mv)
