"""真实 API 连通性测试 — 默认跳过，需显式设置 TEST_REAL_API=1 才执行。

用法：
    # 日常运行（跳过）
    pytest tests/ -v

    # 验证真实 API
    TEST_REAL_API=1 pytest tests/test_real_api.py -v
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_REAL_API"),
    reason="Set TEST_REAL_API=1 to run real API tests",
)


class TestRealAPIConnectivity:

    def test_real_api_connection(self):
        """验证 302.ai 连通性 — 主力模型能正常回复。"""
        from backend.services.llm_client import call_freeform
        result = call_freeform(
            "You are a test assistant. Reply with exactly: PING_OK",
            "Ping"
        )
        assert "PING_OK" in result

    def test_real_api_json_parse(self):
        """验证 JSON 提取链路 — 主力模型能返回合法 JSON。"""
        from backend.services.llm_client import call_structured
        result = call_structured(
            "Return exactly this JSON object: {\"test\": true, \"name\": \"api_check\"}",
            "data"
        )
        assert isinstance(result, dict)
        assert result.get("test") is True

    def test_real_api_skill_call(self):
        """验证 skill 调用链路 — MV01 skill + 表单数据能正常提取。"""
        from backend.services.llm_client import call_skill
        result = call_skill(
            "MV01",
            "Extract deceased name and relation from: 我是孙子，纪念我爷爷李四",
            {"user_input": "我是孙子，纪念我爷爷李四"}
        )
        assert isinstance(result, dict)
        assert "error" not in result or result.get("error") is not True

    def test_real_api_fallback_works(self):
        """验证降级链路 — 即使主力模型失败，fallback 也应可用。

        这个测试不主动触发失败，只记录当前使用的主力+fallback 模型是否都通。
        """
        from backend.services.llm_client import (
            PRIMARY_CLIENT, FALLBACK_CLIENT,
            TEXT_MODELS,
        )
        # 验证两个客户端都能成功返回一个简单响应
        for client, label in [(PRIMARY_CLIENT, "primary"), (FALLBACK_CLIENT, "fallback")]:
            resp = client.chat.completions.create(
                model=TEXT_MODELS.get(label == "primary", "gpt-5.4"),
                messages=[{"role": "user", "content": "Reply: OK"}],
                max_tokens=10,
            )
            assert resp.choices[0].message.content is not None
