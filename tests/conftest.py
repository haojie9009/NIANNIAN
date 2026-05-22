"""pytest conftest — 确保 backend/ 在 sys.path 中。"""
import sys
from pathlib import Path

# backend/ 目录加入 path，使 from routers / from services / from logger 等导入生效
_backend = Path(__file__).resolve().parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# 测试环境禁用启动时的磁盘加载，避免跨测试污染
import unittest.mock as _mock
# 在导入 backend.main 之前 patch load_all
_mock.patch("services.session_store.load_all", return_value=0).start()
