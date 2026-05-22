"""skill_loader 测试 — YAML 加载 + JSON 约束"""

import tempfile
from pathlib import Path

import pytest

from backend.services.skill_loader import load_skill, OUTPUT_CONSTRAINT


class TestLoadSkill:
    def test_loads_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# MV01 Skill\n你是追思助手。")
            f.flush()
            result = load_skill(f.name)
            assert "# MV01 Skill" in result
            assert "你是追思助手。" in result

    def test_appends_json_constraint(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("test content")
            f.flush()
            result = load_skill(f.name)
            assert OUTPUT_CONSTRAINT in result

    def test_strips_trailing_whitespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("content   \n  \n")
            f.flush()
            result = load_skill(f.name)
            # 末尾空白应被去掉，约束接在后面
            assert "content\n\n" in result

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_skill("/nonexistent/skill.md")

    def test_unicode_content(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("姓名：张三，关系：儿子\n情感基调：温暖")
            f.flush()
            result = load_skill(f.name)
            assert "张三" in result
