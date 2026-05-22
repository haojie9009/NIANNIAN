from __future__ import annotations

from typing import Any, Dict, List

import requests


def _base_url(host: str) -> str:
    host = host.strip()
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return f"http://{host}:8188"


def upload_image(
    host: str,
    file_bytes: bytes,
    filename: str,
    subfolder: str = "",
    file_type: str = "input",
) -> str:
    url = f"{_base_url(host)}/upload/image"
    files = {"image": (filename, file_bytes)}
    data = {"subfolder": subfolder, "type": file_type}
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    payload = response.json()
    return payload.get("name") or payload.get("filename") or filename


def ping(host: str) -> bool:
    try:
        url = f"{_base_url(host)}/queue"
        response = requests.get(url, timeout=3)
        return response.ok
    except Exception:
        return False


def submit_prompt(host: str, workflow: Dict[str, Any]) -> str:
    url = f"{_base_url(host)}/prompt"
    response = requests.post(url, json={"prompt": workflow}, timeout=60)
    response.raise_for_status()
    payload = response.json()
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"未返回 prompt_id: {payload}")
    return prompt_id


def get_history(host: str, prompt_id: str) -> Dict[str, Any]:
    url = f"{_base_url(host)}/history/{prompt_id}"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def extract_outputs(history: Dict[str, Any], prompt_id: str) -> List[Dict[str, Any]]:
    prompt_data = history.get(prompt_id, {})
    outputs: List[Dict[str, Any]] = []
    for node in prompt_data.get("outputs", {}).values():
        for key in ("images", "videos"):
            for item in node.get(key, []) if isinstance(node.get(key, []), list) else []:
                outputs.append({
                    "kind": key[:-1],
                    "filename": item.get("filename"),
                    "subfolder": item.get("subfolder", ""),
                    "type": item.get("type", "output"),
                })
    return outputs


def build_view_url(host: str, output: Dict[str, Any]) -> str:
    base = _base_url(host)
    filename = output.get("filename", "")
    subfolder = output.get("subfolder", "")
    file_type = output.get("type", "output")
    return (
        f"{base}/view?filename={filename}"
        f"&subfolder={subfolder}&type={file_type}"
    )
