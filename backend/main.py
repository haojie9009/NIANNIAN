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


from routers import assets, chat, dialogue, intake, pipeline, agent, agent_realtime, auth, memorials, uploads, voice, admin, audio
from core import oss_sync, storage as _storage  # noqa: F401
from logger import app_logger, api_logger
from services import session_store
from services.llm_client import TEXT_MODEL, TEXT_FALLBACK_MODEL, bgm_shutdown


@asynccontextmanager
async def _lifespan(app: FastAPI):
    n = session_store.load_all()
    app_logger.info("session_store: loaded %d sessions from disk", n)
    app_logger.info("LLM 模式: REAL(302.ai), 文本模型=%s/%s", TEXT_MODEL, TEXT_FALLBACK_MODEL)
    # 缓存模式日志
    from services.llm_client import _CACHE_MODE, _cache
    if _CACHE_MODE:
        n_cached = len(_cache.list_cache())
        app_logger.info("LLM 缓存模式: %s (%d 条缓存)", _CACHE_MODE.upper(), n_cached)

    # 后台定时 gc（每 1 小时清理过期 session + 数量上限淘汰）
    async def _gc_loop():
        import asyncio
        while True:
            try:
                await asyncio.sleep(3600)
                n = session_store.gc()
                if n:
                    app_logger.info("[session_store] gc: 清理了 %d 个过期/超限 session", n)
            except asyncio.CancelledError:
                break
            except Exception as e:
                app_logger.exception("[session_store] gc 异常: %s", e)

    import asyncio
    gc_task = asyncio.create_task(_gc_loop())
    # 启动时也跑一次 gc
    removed = session_store.gc()
    if removed:
        app_logger.info("[session_store] startup gc: 清理了 %d 个 session", removed)

    # 修复服务重启后残留的 running 状态 MV06 任务
    stale = session_store.recover_stale_mv06()
    if stale:
        app_logger.info("[session_store] recover_stale_mv06: 修复了 %d 个中断的 MV06 任务", stale)

    chain_stale = session_store.recover_stale_pipeline_chain()
    if chain_stale:
        app_logger.info("[session_store] recover_stale_pipeline_chain: 修复了 %d 个残留 running 的 pipeline_chain", chain_stale)

    try:
        yield
    finally:
        gc_task.cancel()
        bgm_shutdown.set()

app = FastAPI(
    title="念念 NianNian Memorial API",
    version="0.1.0",
    description="念念追思影像平台 — 后端 API（FastAPI），与 Streamlit 共享业务层",
    lifespan=_lifespan,
)

# 启动：自检 + OSS 拉回 + 本地快照恢复（多重保险，确保数据不丢）
@app.on_event("startup")
def _nian_bootstrap():
    from core import storage as _s
    import os, json
    data_dir = _s.DATA_DIR
    print(f"[storage] DATA_DIR = {data_dir}")
    print(f"[storage] NIAN_DATA_DIR env = {os.environ.get('NIAN_DATA_DIR', '(unset)')}")
    print(f"[storage] DATA_DIR exists = {data_dir.exists()}, is mount = {data_dir.is_mount() if hasattr(data_dir, 'is_mount') else 'n/a'}")

    # 1) OSS 镜像拉回（最强保险）
    try:
        if oss_sync.enabled():
            print("[oss] enabled, bootstrap pulling from OSS...")
            oss_sync.bootstrap_pull()
        else:
            print("[oss] DISABLED — 数据仅靠本地 Disk，强烈建议接 OSS 防丢失")
    except Exception as e:
        print(f"[oss] bootstrap failed: {e}")

    # 2) 启动后清点数据，方便用户立刻在 Render Logs 看到
    try:
        _s._ensure()
        users = _s.list_users()
        total_mem = 0
        for u in users:
            try:
                total_mem += len(_s.list_memorials(u.get("user_id", "")))
            except Exception:
                pass
        print(f"[storage] 启动清点：{len(users)} 用户，{total_mem} 个纪念对象")

        # 3) 数据丢失检测：上次有用户、这次没了 → 写报警标记
        marker = data_dir / ".last_user_count"
        if marker.exists():
            try:
                prev = int(marker.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                prev = 0
            if prev > 0 and len(users) == 0:
                print(f"⚠️⚠️⚠️ [DATA LOSS DETECTED] 上次启动有 {prev} 个用户，本次为 0！")
                print(f"⚠️ 请立刻检查 Render Disk 挂载状态和 OSS 配置")
                # 写一个看得到的报警文件
                (data_dir / "DATA_LOSS_WARNING.txt").write_text(
                    f"启动时检测到数据丢失！上次={prev} 当前=0\nDATA_DIR={data_dir}\nNIAN_DATA_DIR={os.environ.get('NIAN_DATA_DIR','')}",
                    encoding="utf-8"
                )
        try:
            marker.write_text(str(len(users)), encoding="utf-8")
        except Exception:
            pass

        # 4) 启动自动快照（保留最近 10 份），写到 DATA_DIR/_backups/
        try:
            _create_startup_snapshot(data_dir)
        except Exception as e:
            print(f"[snapshot] failed: {e}")
    except Exception as e:
        print(f"[storage] bootstrap diag failed: {e}")


def _create_startup_snapshot(data_dir):
    """启动时打个 zip 快照，保留最近 10 份。"""
    import zipfile, time
    from pathlib import Path
    backups = Path(data_dir) / "_backups"
    backups.mkdir(parents=True, exist_ok=True)
    snap = backups / f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    n = 0
    with zipfile.ZipFile(snap, "w", zipfile.ZIP_DEFLATED) as z:
        for p in Path(data_dir).rglob("*"):
            # 排除自己的备份目录，避免雪球
            if "_backups" in p.parts:
                continue
            if p.is_file():
                z.write(p, arcname=str(p.relative_to(data_dir)))
                n += 1
    print(f"[snapshot] created {snap.name} ({n} files)")
    # 清理：只保留最近 10 份
    snaps = sorted(backups.glob("snapshot_*.zip"))
    for old in snaps[:-10]:
        try:
            old.unlink()
        except Exception:
            pass

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
app.include_router(audio.router,    prefix="/api")
app.include_router(admin.router,    prefix="/api")

# 生成资源静态托管零缓存（开发期：重新生成图片/视频后浏览器立即加载新版）
_GENERATED = _BACKEND.parent / "backend" / "outputs" / "generated"
if _GENERATED.exists():
    app.mount("/api/outputs/generated", StaticFiles(directory=str(_GENERATED)), name="generated")

_FINAL = _BACKEND.parent / "backend" / "outputs" / "final_cuts"
_FINAL.mkdir(parents=True, exist_ok=True)
app.mount("/api/outputs/final_cuts", StaticFiles(directory=str(_FINAL)), name="final_cuts")

# 前端 + 生成资源零缓存（开发期：修改后刷新立即生效）
@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    if request.url.path.startswith("/static/") or request.url.path.startswith("/api/outputs/"):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return await call_next(request)

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
        "llm_mode": "real(302.ai)",
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
