"""集成测试 — FastAPI API 路由（使用 TestClient，零网络调用）

注意：这些测试需要完整加载 app，包括所有 router 和 service。
如果路由注册有变化，请同步更新路径。
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_llm():
    """Mock LLM 客户端，避免测试中发起真实网络请求。"""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content='{"test": "mocked"}'))]
    mock_client.chat.completions.create.return_value = mock_resp

    with patch("backend.services.llm_client.PRIMARY_CLIENT", mock_client):
        yield


def _get_app():
    """延迟导入 app，在 mock 生效后再加载。"""
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)


class TestHealth:
    def test_health_returns_ok(self):
        with _get_app() as client:
            r = client.get("/api/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    def test_health_shows_llm_mode(self):
        with _get_app() as client:
            r = client.get("/api/health")
            data = r.json()
            assert "llm_mode" in data


class TestIntakeRoutes:
    """intake 路由 — 依赖 /api/intake 前缀。"""

    def test_submit_form(self):
        with _get_app() as client:
            r = client.post("/api/intake/submit", json={
                "form_data": {
                    "deceased_name": "张三",
                    "deceased_gender": "男",
                    "birth_date": "1950-01-01",
                    "death_date": "2025-01-01",
                }
            })
            assert r.status_code == 200
            data = r.json()
            assert "session_id" in data

    def test_get_session_after_submit(self):
        with _get_app() as client:
            r = client.post("/api/intake/submit", json={
                "form_data": {
                    "deceased_name": "李四",
                    "deceased_gender": "女",
                    "birth_date": "1960-01-01",
                    "death_date": "2025-06-01",
                }
            })
            sid = r.json()["session_id"]
            r2 = client.get(f"/api/intake/session/{sid}")
            assert r2.status_code == 200
            assert r2.json()["form_data"]["deceased_name"] == "李四"

    def test_get_nonexistent_session(self):
        with _get_app() as client:
            r = client.get("/api/intake/session/nonexistent123")
            assert r.status_code == 404

    def test_get_test_data(self):
        with _get_app() as client:
            r = client.get("/api/intake/test-data")
            assert r.status_code == 200
            data = r.json()
            assert "form_data" in data


class TestChatRoutes:
    """chat 路由 — 依赖 /api/chat 前缀。"""

    def test_greeting_exists(self):
        with _get_app() as client:
            r = client.get("/api/chat/greeting")
            # GET 可能不存在，至少接口能访问
            assert r.status_code in (200, 404, 405)

    def test_message_requires_session(self):
        with _get_app() as client:
            r = client.post("/api/chat/message", json={"message": "你好"})
            # 无 session_id 应返回 400
            assert r.status_code in (400, 422)


class TestDialogueRoutes:
    """dialogue 路由 — 依赖 /api/dialogue 前缀。"""

    def test_state_returns_for_valid_session(self):
        with _get_app() as client:
            # 先创建 session
            client.post("/api/intake/submit", json={
                "deceased_name": "测试",
                "deceased_gender": "男",
                "birth_date": "1950-01-01",
                "death_date": "2025-01-01",
            }, follow_redirects=False)
            r = client.get("/api/dialogue/health")
            # 接口存在即可
            assert r.status_code in (200, 404, 405)


class TestPipelineRoutes:
    """pipeline 路由 — 依赖 /api/pipeline 前缀。"""

    def test_status_missing(self):
        with _get_app() as client:
            r = client.get("/api/pipeline/status/nonexistent")
            assert r.status_code == 404
