"""session_store 持久化测试 — 创建/读写/加载/过期"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.session_store import (
    _SESSIONS,
    create_session,
    gc,
    get,
    load_all,
    patch_form,
    require,
    update,
)


@pytest.fixture(autouse=True)
def _clean():
    """每次测试前清空内存 session。"""
    _SESSIONS.clear()
    yield
    _SESSIONS.clear()


class TestCreateSession:
    def test_returns_valid_id(self):
        sid = create_session()
        assert len(sid) == 32  # uuid4.hex

    def test_initial_state(self):
        sid = create_session({"name": "test"})
        s = get(sid)
        assert s["form_data"]["name"] == "test"
        assert s["assets"] == []
        assert s["chat_history"] == []
        assert s["gate"] is not None
        assert s["pipeline_state"] is not None

    def test_empty_form(self):
        sid = create_session()
        s = get(sid)
        assert s["form_data"] == {}

    def test_persists_to_disk(self):
        sid = create_session({"name": "disk_test"})
        import os
        data_dir = Path(__file__).resolve().parent.parent / "backend" / "data" / "sessions"
        path = data_dir / f"{sid}.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["session_id"] == sid


class TestGetRequire:
    def test_get_existing(self):
        sid = create_session()
        assert get(sid) is not None

    def test_get_missing(self):
        assert get("nonexistent") is None

    def test_require_existing(self):
        sid = create_session()
        data = require(sid)
        assert isinstance(data, dict)

    def test_require_missing_raises_keyerror(self):
        with pytest.raises(KeyError, match="session not found"):
            require("nonexistent")


class TestUpdate:
    def test_update_field(self):
        sid = create_session()
        s = update(sid, preview_text="hello")
        assert s["preview_text"] == "hello"

    def test_update_missing_raises_keyerror(self):
        with pytest.raises(KeyError, match="session not found"):
            update("nonexistent", preview_text="x")

    def test_update_touches_timestamp(self):
        sid = create_session()
        old_ts = get(sid)["updated_at"]
        time.sleep(0.01)
        update(sid, preview_text="x")
        assert get(sid)["updated_at"] > old_ts


class TestPatchForm:
    def test_patch_adds_field(self):
        sid = create_session()
        patch_form(sid, {"deceased_name": "张三"})
        assert get(sid)["form_data"]["deceased_name"] == "张三"

    def test_patch_skips_empty_value(self):
        sid = create_session({"name": "keep"})
        patch_form(sid, {"name": "", "extra": None})
        assert get(sid)["form_data"]["name"] == "keep"
        assert "extra" not in get(sid)["form_data"]

    def test_patch_missing_raises_keyerror(self):
        with pytest.raises(KeyError):
            patch_form("nonexistent", {"x": 1})


class TestLoadAll:
    def test_load_from_disk(self, tmp_path):
        """从磁盘加载 session。"""
        sid = "test_load_abc123"
        data = {
            "session_id": sid,
            "created_at": time.time(),
            "updated_at": time.time(),
            "form_data": {"name": "from_disk"},
            "assets": [],
            "chat_history": [],
            "mv_outputs": {},
            "preview_text": "",
            "gate": {},
            "pipeline_state": {},
            "ds_chat": [],
            "ds_result": None,
            "dialogue": {},
        }
        (tmp_path / f"{sid}.json").write_text(json.dumps(data))

        with patch("backend.services.session_store._DATA_DIR", tmp_path):
            count = load_all()
            assert count == 1
            assert get(sid) is not None

    def test_load_skips_expired(self, tmp_path):
        """过期 session 不应加载。"""
        sid = "test_expired_xyz"
        data = {
            "session_id": sid,
            "created_at": time.time() - 10 * 24 * 3600,  # 10 天前
            "updated_at": time.time() - 10 * 24 * 3600,
            "form_data": {},
            "assets": [],
            "chat_history": [],
            "mv_outputs": {},
            "preview_text": "",
            "gate": {},
            "pipeline_state": {},
            "ds_chat": [],
            "ds_result": None,
            "dialogue": {},
        }
        (tmp_path / f"{sid}.json").write_text(json.dumps(data))

        with patch("backend.services.session_store._DATA_DIR", tmp_path):
            count = load_all()
            assert count == 0
            assert get(sid) is None


class TestGC:
    def test_gc_removes_expired(self):
        sid = create_session()
        # 手动改时间为过期
        _SESSIONS[sid]["updated_at"] = time.time() - 8 * 24 * 3600
        removed = gc()
        assert removed == 1
        assert get(sid) is None

    def test_gc_keeps_fresh(self):
        sid = create_session()
        removed = gc()
        assert removed == 0
        assert get(sid) is not None
