"""llm_client 纯逻辑测试 — JSON 解析 / 缓存 / 降级重试"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from backend.services.llm_client import (
    _ResponseCache,
    _extract_json,
    _json_system_hint,
    _supports_json_mode,
)


# ──────────────────────────────────────────────
# _supports_json_mode
# ──────────────────────────────────────────────
class TestSupportsJsonMode:
    def test_openai_supports_json(self):
        assert _supports_json_mode("gpt-4o") is True
        assert _supports_json_mode("gpt-5.4") is True
        assert _supports_json_mode("gpt-3.5-turbo") is True

    def test_claude_no_json(self):
        assert _supports_json_mode("claude-sonnet-4-6") is False
        assert _supports_json_mode("claude-3-opus-20240229") is False

    def test_gemini_no_json(self):
        assert _supports_json_mode("gemini-2.0-pro-image-preview") is False
        assert _supports_json_mode("gemini-3-pro-image-preview") is False

    def test_case_insensitive(self):
        assert _supports_json_mode("CLAUDE-Sonnet-4-6") is False
        assert _supports_json_mode("GPT-4o") is True


# ──────────────────────────────────────────────
# _extract_json
# ──────────────────────────────────────────────
class TestExtractJson:
    def test_code_block_json(self):
        text = """Here is the result:\n```json\n{"name": "test", "age": 30}\n```"""
        assert _extract_json(text) == '{"name": "test", "age": 30}'

    def test_code_block_without_lang(self):
        text = "```\n{\"key\": \"value\"}\n```"
        assert _extract_json(text) == '{"key": "value"}'

    def test_bare_json_braces(self):
        text = "Sure! Here you go:\n\n{'name': 'test'}\n\nHope that helps."
        result = _extract_json(text)
        assert result.startswith("{") and result.endswith("}")
        assert "'name': 'test'" in result

    def test_already_json(self):
        text = '{"clean": true}'
        assert _extract_json(text) == '{"clean": true}'

    def test_array_in_code_block(self):
        text = "```json\n[1, 2, 3]\n```"
        assert _extract_json(text) == '[1, 2, 3]'

    def test_no_braces_returns_stripped(self):
        text = "  hello world  "
        assert _extract_json(text) == "hello world"


# ──────────────────────────────────────────────
# _json_system_hint
# ──────────────────────────────────────────────
class TestJsonSystemHint:
    def test_appends_hint(self):
        result = _json_system_hint("Do analysis.")
        assert "必须是且仅是一个合法的 JSON" in result

    def test_strips_trailing_whitespace(self):
        result = _json_system_hint("Do analysis.   \n  ")
        assert result.startswith("Do analysis.\n")


# ──────────────────────────────────────────────
# _ResponseCache — 缓存读写
# ──────────────────────────────────────────────
class TestResponseCache:
    @pytest.fixture
    def cache(self):
        """Create a cache backed by a temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            c = _ResponseCache()
            c._CACHE_DIR = tmpdir
            yield c

    def test_save_and_load(self, cache):
        cache.save("chat/test", {"reply": "hello"}, "gpt-4o", {"key": "val"})
        result = cache.load("chat/test", "gpt-4o", {"key": "val"})
        assert result == {"reply": "hello"}

    def test_load_miss_returns_none(self, cache):
        assert cache.load("chat/nonexistent", "gpt-4o", {"x": 1}) is None

    def test_different_params_miss(self, cache):
        cache.save("chat/test", {"reply": "A"}, "gpt-4o", {"skill": "MV01"})
        assert cache.load("chat/test", "gpt-4o", {"skill": "MV02"}) is None

    def test_cache_key_deterministic(self, cache):
        k1 = cache._cache_key("chat/c", "m1", {"a": 1})
        k2 = cache._cache_key("chat/c", "m1", {"a": 1})
        assert k1 == k2

    def test_cache_key_order_independent(self, cache):
        k1 = cache._cache_key("chat/c", "m", {"a": 1, "b": 2})
        k2 = cache._cache_key("chat/c", "m", {"b": 2, "a": 1})
        assert k1 == k2

    def test_list_cache(self, cache):
        cache.save("chat/a", {"r": 1}, "m1", {"x": 1})
        cache.save("chat/b", {"r": 2}, "m2", {"x": 2})
        items = cache.list_cache()
        assert len(items) == 2
        assert all("file" in i and "endpoint" in i for i in items)

    def test_list_empty_dir(self, cache):
        assert cache.list_cache() == []

    def test_video_cache(self, cache):
        data = {"url": "https://mock.video/v.mp4", "task_id": "t1"}
        cache.save("video/gen", data, "kling-v3", {"prompt": "a cat"})
        result = cache.load("video/gen", "kling-v3", {"prompt": "a cat"})
        assert result["url"] == "https://mock.video/v.mp4"


# ──────────────────────────────────────────────
# call_skill — 降级 / 重试 / 缓存
# ──────────────────────────────────────────────
class TestCallSkill:
    @pytest.fixture(autouse=True)
    def _setup(self):
        """Patch the global PRIMARY_CLIENT to avoid real network calls."""
        self._mock_client = MagicMock()
        with patch("backend.services.llm_client.PRIMARY_CLIENT", self._mock_client):
            yield

    def _mock_success(self, content):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        self._mock_client.chat.completions.create.return_value = resp

    def _mock_fail(self):
        self._mock_client.chat.completions.create.side_effect = Exception("Connection error")

    def test_first_model_succeeds(self):
        """主力模型一次成功，不应触发降级。"""
        self._mock_success('{"name": "test"}')

        from backend.services.llm_client import call_skill
        result = call_skill("MV01", "extract info", {"name": "John"})

        assert result == {"name": "test"}
        # 只调用了一次
        assert self._mock_client.chat.completions.create.call_count == 1

    def test_first_model_falls_back_to_second(self):
        """主力 3 次失败 → 自动降级到 fallback 模型。"""
        call_count = [0]

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise Exception("Connection timeout")
            return MagicMock(choices=[MagicMock(message=MagicMock(content='{"ok": true}'))])

        self._mock_client.chat.completions.create.side_effect = _side_effect

        from backend.services.llm_client import call_skill
        result = call_skill("MV01", "extract", {"x": 1})

        assert result == {"ok": True}
        # 3 次主力失败 + 1 次 fallback 成功 = 4 次
        assert call_count[0] == 4

    def test_all_models_fail_returns_error(self):
        """所有模型都失败 → 返回 error dict。"""
        self._mock_fail()

        from backend.services.llm_client import call_skill
        result = call_skill("MV01", "extract", {"x": 1})

        assert result.get("error") is True
        assert "Connection error" in result.get("message", "")

    def test_mv04_uses_storyboard_queue(self):
        """MV04 应使用 storyboard 专用队列 (gpt-4o)。"""
        from backend.services.llm_client import _storyboard_model_queue

        queue = _storyboard_model_queue()
        assert queue[0][0] != "claude-sonnet-4-6"  # 不应以 claude 开头
        assert "gpt-4o" in queue[0][0]

    def test_playback_returns_cached(self):
        """playback 模式应直接返回缓存，不调用 API。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_cache = _ResponseCache()
            mock_cache._CACHE_DIR = tmpdir
            mock_cache.save("chat/completions", {"cached": True}, "claude-sonnet-4-6",
                            {"skill": "MV01", "payload": {"x": 1}})

            with patch("backend.services.llm_client._CACHE_MODE", "playback"), \
                 patch("backend.services.llm_client._cache", mock_cache):
                from backend.services.llm_client import call_skill
                result = call_skill("MV01", "extract", {"x": 1})

                assert result == {"cached": True}
                # 不应调用 API
                self._mock_client.chat.completions.create.assert_not_called()

    def test_record_saves_response(self):
        """record 模式应在成功后保存响应。"""
        self._mock_success('{"name": "recorded"}')

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_cache = _ResponseCache()
            mock_cache._CACHE_DIR = tmpdir

            with patch("backend.services.llm_client._CACHE_MODE", "record"), \
                 patch("backend.services.llm_client._cache", mock_cache):
                from backend.services.llm_client import call_skill
                result = call_skill("MV01", "extract", {"name": "test"})

                assert result == {"name": "recorded"}
                # 缓存中应有数据
                items = mock_cache.list_cache()
                assert len(items) == 1
                assert items[0]["endpoint"] == "chat/completions"


# ──────────────────────────────────────────────
# call_freeform / call_structured — 降级逻辑
# ──────────────────────────────────────────────
class TestCallFreeformStructured:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self._mock_client = MagicMock()
        with patch("backend.services.llm_client.PRIMARY_CLIENT", self._mock_client):
            yield

    def test_freeform_success(self):
        self._mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Hello world"))]
        )

        from backend.services.llm_client import call_freeform
        result = call_freeform("You are helpful.", "Say hello.")
        assert result == "Hello world"

    def test_freeform_all_fail_returns_error_string(self):
        self._mock_client.chat.completions.create.side_effect = Exception("timeout")

        from backend.services.llm_client import call_freeform
        result = call_freeform("You are helpful.", "Say hello.")
        assert result.startswith("[ERROR]")
        assert "timeout" in result

    def test_structured_success(self):
        self._mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"key": "value"}'))]
        )

        from backend.services.llm_client import call_structured
        result = call_structured("Extract JSON.", "data here")
        assert result == {"key": "value"}

    def test_structured_all_fail_returns_error_dict(self):
        self._mock_client.chat.completions.create.side_effect = Exception("timeout")

        from backend.services.llm_client import call_structured
        result = call_structured("Extract JSON.", "data here")
        assert result.get("error") is True


# ──────────────────────────────────────────────
# call_storyboard — gpt-4o 队列 + 降级
# ──────────────────────────────────────────────
class TestCallStoryboard:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self._mock_client = MagicMock()
        with patch("backend.services.llm_client.PRIMARY_CLIENT", self._mock_client):
            yield

    def test_gpt4o_json_mode_no_extraction_needed(self):
        """gpt-4o 支持 JSON mode，应直接返回。"""
        self._mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"image_prompt": "test", "video_prompt": "move"}'))]
        )

        from backend.services.llm_client import call_storyboard
        result = call_storyboard("Make storyboard.", "scene data")
        assert result["image_prompt"] == "test"
        assert result["video_prompt"] == "move"

    def test_fallback_to_gpt54(self):
        """gpt-4o 失败 → 降级到 gpt-5.4。"""
        call_count = [0]

        def _side(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise Exception("rate limit")
            return MagicMock(choices=[MagicMock(message=MagicMock(
                content='{"image_prompt": "fallback", "video_prompt": "pan"}'
            ))])

        self._mock_client.chat.completions.create.side_effect = _side

        from backend.services.llm_client import call_storyboard
        result = call_storyboard("Make storyboard.", "scene data")
        assert result["image_prompt"] == "fallback"
