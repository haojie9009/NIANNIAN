from typing import Any, Dict, List

import streamlit as st

GATE_ORDER: List[str] = ["MV01", "MV02", "MV03", "MV04", "MV05", "MV06"]


def _init_state() -> None:
    if "gate_status" not in st.session_state:
        st.session_state["gate_status"] = {gate: "pending" for gate in GATE_ORDER}
    if "gate_rejections" not in st.session_state:
        st.session_state["gate_rejections"] = {gate: {} for gate in GATE_ORDER}


def get_status(gate: str) -> str:
    _init_state()
    return st.session_state["gate_status"].get(gate, "pending")


def set_running(gate: str) -> None:
    _init_state()
    st.session_state["gate_status"][gate] = "running"


def set_awaiting_review(gate: str) -> None:
    _init_state()
    st.session_state["gate_status"][gate] = "awaiting_review"


def approve(gate: str) -> None:
    _init_state()
    st.session_state["gate_status"][gate] = "approved"
    st.session_state["gate_rejections"][gate] = {}


def reject(gate: str, scope: Dict[str, Any]) -> None:
    _init_state()
    st.session_state["gate_status"][gate] = "rejected"
    st.session_state["gate_rejections"][gate] = scope


def can_run(gate: str) -> bool:
    _init_state()
    if gate not in GATE_ORDER:
        return False
    index = GATE_ORDER.index(gate)
    if index == 0:
        return True
    prev_gate = GATE_ORDER[index - 1]
    return st.session_state["gate_status"].get(prev_gate) == "approved"


def reset_from(gate: str) -> None:
    _init_state()
    if gate not in GATE_ORDER:
        return
    start_index = GATE_ORDER.index(gate)
    for item in GATE_ORDER[start_index:]:
        st.session_state["gate_status"][item] = "pending"
        st.session_state["gate_rejections"][item] = {}
