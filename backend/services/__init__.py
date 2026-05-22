# backend/services/__init__.py
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # 项目根（供 service_manager 引用）
