# backend/main.py — FastAPI 入口
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent        # backend/ 的绝对路径
_ROOT    = _BACKEND.parent                         # 项目根的绝对路径

# 同时注入 backend/ 和项目根，兼容「cd backend; uvicorn main:app」和「uvicorn backend.main:app」
for _p in (_BACKEND, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


from routers import assets, chat, dialogue, intake, pipeline, agent, agent_realtime, auth, memorials, uploads, voice
from core import oss_sync, storage as _storage  # noqa: F401
from logger import app_logger, api_logger
from services import session_store
from services.llm_client import _MOCK_MODE, TEXT_MODEL, TEXT_FALLBACK_MODEL


@asynccontextmanager
async def _lifespan(app: FastAPI):
    n = session_store.load_all()
    app_logger.info("session_store: loaded %d sessions from disk", n)
    if _MOCK_MODE:
        app_logger.info("[WARN] LLM 运行在 MOCK 模式，所有请求使用本地模拟")
    else:
        app_logger.info("LLM 模式: REAL(302.ai), 文本模型=%s/%s", TEXT_MODEL, TEXT_FALLBACK_MODEL)
    # 缓存模式日志
    from services.llm_client import _CACHE_MODE, _cache
    if _CACHE_MODE:
        n_cached = len(_cache.list_cache())
        app_logger.info("LLM 缓存模式: %s (%d 条缓存)", _CACHE_MODE.upper(), n_cached)
    yield

app = FastAPI(
    title="念念 NianNian Memorial API",
    version="0.1.0",
    description="念念追思影像平台 — 后端 API（FastAPI），与 Streamlit 共享业务层",
    lifespan=_lifespan,
)

# 启动：如启用 OSS，则从 OSS 镜像拉回本地（容器重启/扩容不丢数据）
@app.on_event("startup")
def _nian_bootstrap():
    try:
        if oss_sync.enabled():
            print("[oss] enabled, bootstrap pulling from OSS...")
            oss_sync.bootstrap_pull()
        else:
            print("[oss] disabled (using local filesystem only)")
    except Exception as e:
        print(f"[oss] bootstrap failed: {e}")

# CORS（开发阶段全开，生产收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
        elapsed = (time.time() - start) * 1000
        api_logger.info("%s %s %d %.0fms", request.method, request.url.path, response.status_code, elapsed)
        return response
    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        api_logger.exception("%s %s ERROR %.0fms — %s", request.method, request.url.path, elapsed, exc)
        raise

# 路由注册
app.include_router(intake.router,   prefix="/api")
app.include_router(chat.router,     prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(assets.router,   prefix="/api")
app.include_router(dialogue.router, prefix="/api")
app.include_router(agent.router,    prefix="/api")
app.include_router(agent_realtime.router, prefix="/api")
app.include_router(auth.router,     prefix="/api")
app.include_router(memorials.router, prefix="/api")
app.include_router(uploads.router,  prefix="/api")
app.include_router(voice.router,    prefix="/api")

# 生成资源静态托管（图片/视频本地持久化）
_GENERATED = _BACKEND.parent / "backend" / "outputs" / "generated"
if _GENERATED.exists():
    app.mount("/api/outputs/generated", StaticFiles(directory=str(_GENERATED)), name="generated")

# 前端静态文件（开发期直接由后端托管，生产可分离至 Nginx/CDN）
_FRONTEND = _BACKEND.parent / "frontend"
if _FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    # 根路径直接跳转到首页（让 http://localhost:8000 = 主程序）
    @app.get("/")
    def root_index():
        return RedirectResponse(url="/static/index.html", status_code=307)

    # 便捷路径：/memorial /deep_search /dialogue → 对应 HTML
    @app.get("/memorial")
    def page_memorial():
        return RedirectResponse(url="/static/memorial.html", status_code=307)

    @app.get("/deep_search")
    def page_deep_search():
        return RedirectResponse(url="/static/deep_search.html", status_code=307)

    @app.get("/dialogue")
    def page_dialogue():
        return RedirectResponse(url="/static/dialogue.html", status_code=307)

    @app.get("/pipeline")
    def page_pipeline():
        return RedirectResponse(url="/static/pipeline.html", status_code=307)

    @app.get("/studio")
    def page_studio():
        return RedirectResponse(url="/static/studio.html", status_code=307)

    @app.get("/login")
    def page_login():
        return RedirectResponse(url="/static/login.html", status_code=307)

    @app.get("/library")
    def page_library():
        return RedirectResponse(url="/static/library.html", status_code=307)

    @app.get("/voice")
    def page_voice():
        return RedirectResponse(url="/static/voice_studio.html", status_code=307)
else:
    @app.get("/")
    def root_no_frontend() -> JSONResponse:
        return JSONResponse({"service": "念念 NianNian Memorial API", "docs": "/docs"})


@app.get("/api/health")
def health() -> dict:
    app_logger.info("health check")
    return {
        "status": "ok",
        "service": "niannian-backend",
        "llm_mode": "mock" if _MOCK_MODE else "real(302.ai)",
        "text_models": [TEXT_MODEL, TEXT_FALLBACK_MODEL],
    }


# ── 缓存管理（用于 record → playback 工作流）─────────────────────────────────

@app.get("/api/cache")
def cache_list():
    """查看已缓存的 API 响应列表"""
    from services.llm_client import _cache
    return {"count": len(_cache.list_cache()), "items": _cache.list_cache()}


@app.delete("/api/cache/{filename}")
def cache_delete(filename: str):
    """删除指定缓存文件"""
    import os
    from services.llm_client import _cache
    path = os.path.join(_cache._CACHE_DIR, filename)
    if os.path.isfile(path):
        os.remove(path)
        return {"ok": True, "deleted": filename}
    return {"ok": False, "error": "not found"}


@app.delete("/api/cache")
def cache_clear():
    """清空所有缓存"""
    import os, shutil
    from services.llm_client import _cache
    if os.path.isdir(_cache._CACHE_DIR):
        shutil.rmtree(_cache._CACHE_DIR)
    return {"ok": True, "cleared": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
