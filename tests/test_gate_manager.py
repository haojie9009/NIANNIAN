"""gate_manager 纯逻辑测试 — 门控状态机"""

import pytest
from backend.services.gate_manager import (
    GATE_ORDER,
    new_state,
    get_status,
    set_running,
    set_awaiting_review,
    approve,
    reject,
    can_run,
    reset_from,
)


class TestNewState:
    def test_all_gates_pending(self):
        state = new_state()
        for g in GATE_ORDER:
            assert state["gate_status"][g] == "pending"

    def test_rejections_empty(self):
        state = new_state()
        for g in GATE_ORDER:
            assert state["gate_rejections"][g] == {}

    def test_independent_instances(self):
        s1 = new_state()
        s2 = new_state()
        s1["gate_status"]["MV01"] = "approved"
        assert s2["gate_status"]["MV01"] == "pending"


class TestGetStatus:
    def test_default_pending(self):
        state = new_state()
        assert get_status(state, "MV01") == "pending"

    def test_after_running(self):
        state = new_state()
        set_running(state, "MV02")
        assert get_status(state, "MV02") == "running"

    def test_unknown_gate(self):
        state = new_state()
        assert get_status(state, "MV99") == "pending"


class TestCanRun:
    def test_first_always_true(self):
        state = new_state()
        assert can_run(state, "MV01") is True

    def test_second_blocked_initially(self):
        state = new_state()
        assert can_run(state, "MV02") is False

    def test_second_allowed_after_first_approved(self):
        state = new_state()
        approve(state, "MV01")
        assert can_run(state, "MV02") is True

    def test_third_blocked_if_second_pending(self):
        state = new_state()
        approve(state, "MV01")
        assert can_run(state, "MV03") is False

    def test_third_allowed_after_second_approved(self):
        state = new_state()
        approve(state, "MV01")
        approve(state, "MV02")
        assert can_run(state, "MV03") is True

    def test_blocked_after_rejection(self):
        state = new_state()
        reject(state, "MV01", {"reason": "test"})
        assert can_run(state, "MV02") is False

    def test_blocked_after_running(self):
        state = new_state()
        set_running(state, "MV01")
        assert can_run(state, "MV02") is False

    def test_blocked_after_awaiting_review(self):
        state = new_state()
        set_awaiting_review(state, "MV01")
        assert can_run(state, "MV02") is False

    def test_unknown_gate_false(self):
        state = new_state()
        assert can_run(state, "MV99") is False

    def test_full_chain(self):
        """逐个 approve，验证后续 gate 依次解锁"""
        state = new_state()
        for i, g in enumerate(GATE_ORDER):
            assert can_run(state, g) is True
            approve(state, g)
            if i + 1 < len(GATE_ORDER):
                assert can_run(state, GATE_ORDER[i + 1]) is True


class TestStateTransitions:
    def test_running_to_awaiting_review(self):
        state = new_state()
        set_running(state, "MV01")
        assert get_status(state, "MV01") == "running"
        set_awaiting_review(state, "MV01")
        assert get_status(state, "MV01") == "awaiting_review"

    def test_awaiting_review_to_approved(self):
        state = new_state()
        set_awaiting_review(state, "MV01")
        approve(state, "MV01")
        assert get_status(state, "MV01") == "approved"

    def test_rejection_clears_previous(self):
        state = new_state()
        reject(state, "MV02", {"scope": {"field": "name"}})
        assert get_status(state, "MV02") == "rejected"
        assert state["gate_rejections"]["MV02"] == {"scope": {"field": "name"}}

    def test_approved_clears_rejections(self):
        state = new_state()
        reject(state, "MV01", {"reason": "bad"})
        approve(state, "MV01")
        assert state["gate_rejections"]["MV01"] == {}


class TestResetFrom:
    def test_reset_all(self):
        state = new_state()
        approve(state, "MV01")
        approve(state, "MV02")
        reset_from(state, "MV01")
        for g in GATE_ORDER:
            assert get_status(state, g) == "pending"

    def test_reset_middle(self):
        state = new_state()
        approve(state, "MV01")
        approve(state, "MV02")
        approve(state, "MV03")
        reset_from(state, "MV02")
        assert get_status(state, "MV01") == "approved"
        assert get_status(state, "MV02") == "pending"
        assert get_status(state, "MV03") == "pending"

    def test_reset_last(self):
        state = new_state()
        approve(state, "MV01")
        approve(state, "MV02")
        approve(state, "MV03")
        approve(state, "MV04")
        approve(state, "MV05")
        approve(state, "MV06")
        reset_from(state, "MV06")
        assert get_status(state, "MV05") == "approved"
        assert get_status(state, "MV06") == "pending"

    def test_reset_unknown_gate_noop(self):
        state = new_state()
        approve(state, "MV01")
        reset_from(state, "MV99")
        assert get_status(state, "MV01") == "approved"
        assert get_status(state, "MV02") == "pending"

    def test_reset_clears_rejections(self):
        state = new_state()
        reject(state, "MV03", {"scope": "x"})
        reset_from(state, "MV03")
        assert state["gate_rejections"]["MV03"] == {}
