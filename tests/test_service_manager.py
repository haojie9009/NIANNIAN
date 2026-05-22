"""service_manager 纯逻辑测试 — 表单摘要、聊天记录解析、分镜提取、人设构建"""

import pytest
import json
from backend.services.service_manager import (
    _form_summary_for_ai,
    parse_chat_file,
    _get_scenes_from_mv04,
    build_persona_system_prompt,
)


class TestFormSummaryForAI:
    """_form_summary_for_ai 将表单数据转为摘要文本"""

    def test_empty_form(self):
        result = _form_summary_for_ai({})
        assert "尚未填写" in result or "详细信息" in result

    def test_full_form(self):
        data = {
            "deceased_name": "张三",
            "speaker_relation": "女儿",
            "birth_date": "1950年1月1日",
            "death_date": "2024年1月1日",
            "occupation": "教师",
            "family_memory_text": "父亲一生热爱教育事业",
            "last_wishes": "希望家人和睦",
        }
        result = _form_summary_for_ai(data)
        assert "张三" in result
        assert "女儿" in result
        assert "1950年1月1日" in result
        assert "2024年1月1日" in result
        assert "教师" in result
        assert "家庭回忆" in result
        assert "心愿" in result

    def test_living_person(self):
        """在世的人（没有 death_date）"""
        data = {
            "deceased_name": "李四",
            "birth_date": "1960年6月6日",
        }
        result = _form_summary_for_ai(data)
        assert "在世" in result

    def test_memory_truncated(self):
        """长回忆文本应被截断"""
        long_text = "A" * 500
        data = {"family_memory_text": long_text}
        result = _form_summary_for_ai(data)
        # 应该截断到 300 字以内
        assert len(result) < 400

    def test_wishes_truncated(self):
        long_text = "B" * 300
        data = {"last_wishes": long_text}
        result = _form_summary_for_ai(data)
        assert len(result) < 250

    def test_partial_form(self):
        data = {"deceased_name": "王五"}
        result = _form_summary_for_ai(data)
        assert "王五" in result
        assert "发言人" not in result
        assert "出生" not in result


class TestParseChatFile:
    """parse_chat_file 解析不同格式的聊天记录"""

    def test_csv_wechat_format(self):
        """微信导出的 CSV 格式"""
        csv_data = (
            "StrTalker,IsSender,Type,StrContent\n"
            "张三,0,1,今天天气真好\n"
            "李四,1,1,是啊，出去走走\n"
            "张三,0,1,我们去公园吧\n"
        )
        messages = parse_chat_file(csv_data.encode("utf-8"), "chat.csv", "")
        # IsSender=0 是对方的消息
        assert len(messages) == 2
        assert messages[0]["sender"] == "张三"
        assert messages[0]["content"] == "今天天气真好"

    def test_csv_filters_by_target(self):
        """按 target 名称过滤"""
        csv_data = (
            "StrTalker,IsSender,Type,StrContent\n"
            "张三,0,1,消息1\n"
            "王五,0,1,消息2\n"
        )
        messages = parse_chat_file(csv_data.encode("utf-8"), "chat.csv", "张三")
        assert len(messages) == 1
        assert messages[0]["sender"] == "张三"

    def test_csv_skips_non_text(self):
        """Type != 1 的消息应该跳过（图片、语音等）"""
        csv_data = (
            "StrTalker,IsSender,Type,StrContent\n"
            "张三,0,3,<img>\n"
            "张三,0,1,文字消息\n"
        )
        messages = parse_chat_file(csv_data.encode("utf-8"), "chat.csv", "")
        assert len(messages) == 1

    def test_json_array_format(self):
        """JSON 数组格式"""
        data = json.dumps([
            {"sender": "Alice", "content": "你好", "IsSender": "0", "Type": "1"},
            {"sender": "Bob", "content": "嗨", "IsSender": "1", "Type": "1"},
            {"sender": "Alice", "content": "最近好吗", "IsSender": "0", "Type": "1"},
        ])
        messages = parse_chat_file(data.encode("utf-8"), "chat.json", "")
        assert len(messages) == 2
        assert messages[0]["content"] == "你好"

    def test_json_nested_object(self):
        """JSON 对象内含 messages 数组"""
        data = json.dumps({
            "messages": [
                {"sender": "妈", "content": "吃饭了吗", "IsSender": "0", "Type": "1"},
            ]
        })
        messages = parse_chat_file(data.encode("utf-8"), "chat.json", "")
        assert len(messages) == 1
        assert messages[0]["sender"] == "妈"

    def test_json_fallback_keys(self):
        """尝试多种 key 查找消息列表"""
        for key in ["msg", "records", "data"]:
            data = json.dumps({
                key: [
                    {"sender": "X", "content": "test", "IsSender": "0", "Type": "1"},
                ]
            })
            messages = parse_chat_file(data.encode("utf-8"), "chat.json", "")
            assert len(messages) == 1

    def test_txt_format(self):
        """TXT 聊天记录格式"""
        txt = (
            "[2024-01-01 10:00:00] 张三(12345678): 你好啊\n"
            "[2024-01-01 10:01:00] 李四(87654321): 你好\n"
        )
        messages = parse_chat_file(txt.encode("utf-8"), "chat.txt", "张三")
        assert len(messages) == 1
        assert messages[0]["content"] == "你好啊"

    def test_empty_file(self):
        assert parse_chat_file(b"", "empty.txt", "") == []

    def test_invalid_json(self):
        assert parse_chat_file(b"not json at all", "chat.json", "") == []


class TestGetScenesFromMv04:
    """_get_scenes_from_mv04 从 MV04 输出中提取分镜列表"""

    def test_none_input(self):
        assert _get_scenes_from_mv04(None) == []

    def test_non_dict_input(self):
        assert _get_scenes_from_mv04("hello") == []

    def test_scenes_as_list(self):
        mv04 = {"scenes": [{"id": 1}, {"id": 2}]}
        result = _get_scenes_from_mv04(mv04)
        assert len(result) == 2

    def test_scenes_as_dict_sorted(self):
        mv04 = {
            "scenes": {
                "3": {"id": 3},
                "1": {"id": 1},
                "2": {"id": 2},
            }
        }
        result = _get_scenes_from_mv04(mv04)
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2
        assert result[2]["id"] == 3

    def test_storyboard_fallback(self):
        mv04 = {"storyboard": [{"shot": 1}, {"shot": 2}]}
        result = _get_scenes_from_mv04(mv04)
        assert len(result) == 2

    def test_filters_non_dict_items(self):
        mv04 = {"scenes": [{"id": 1}, "bad", 42, None]}
        result = _get_scenes_from_mv04(mv04)
        assert len(result) == 1
        assert result[0]["id"] == 1


class TestBuildPersonaSystemPrompt:
    """build_persona_system_prompt 构建数字人对话的系统提示"""

    def test_basic_structure(self):
        dna = {
            "speech_patterns": ["嗯", "好吧"],
            "tone": "温和",
            "humor_level": 3,
            "typical_topics": ["做饭", "种花"],
        }
        prompt = build_persona_system_prompt(dna, "李奶奶")
        assert "李奶奶" in prompt
        assert "嗯" in prompt
        assert "温和" in prompt
        assert "3/5" in prompt
        assert "做饭" in prompt

    def test_empty_dna(self):
        prompt = build_persona_system_prompt({}, "TA")
        assert "TA" in prompt
        # 应该不崩溃，使用默认值

    def test_signature_phrases(self):
        dna = {
            "signature_phrases": ["活着就好", "平平淡淡才是真"],
            "tone": "朴实",
        }
        prompt = build_persona_system_prompt(dna, "爷爷")
        assert "活着就好" in prompt
        assert "平平淡淡才是真" in prompt

    def test_emotional_words(self):
        dna = {
            "emotional_words": ["心疼", "欣慰"],
            "tone": "慈爱",
        }
        prompt = build_persona_system_prompt(dna, "母亲")
        assert "心疼" in prompt
        assert "欣慰" in prompt

    def test_extra_description(self):
        dna = {"tone": "温暖"}
        prompt = build_persona_system_prompt(dna, "父亲", "他是一名退休医生")
        assert "退休医生" in prompt
        assert "角色背景补充" in prompt

    def test_no_extra_description_when_empty(self):
        dna = {"tone": "温暖"}
        prompt = build_persona_system_prompt(dna, "父亲", "")
        assert "角色背景补充" not in prompt
