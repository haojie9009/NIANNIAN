# backend/routers/admin.py — 管理员数据安全：手动快照 / 列表 / 下载 / 恢复
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pathlib import Path
import os, time, zipfile, shutil

from core import security, storage

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_owner(user=Depends(security.get_current_user)):
    if not user.get("is_owner"):
        raise HTTPException(403, "仅主人可访问")
    return user


def _backups_dir() -> Path:
    p = storage.DATA_DIR / "_backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/data/inspect")
def inspect(user=Depends(_require_owner)):
    """看数据目录的健康状况：路径、用户数、备份数、磁盘空间。"""
    d = storage.DATA_DIR
    users = storage.list_users() if d.exists() else []
    total_mem = 0
    for u in users:
        try:
            total_mem += len(storage.list_memorials(u.get("user_id", "")))
        except Exception:
            pass
    try:
        usage = shutil.disk_usage(str(d))
        free_mb = usage.free // (1024 * 1024)
        total_mb = usage.total // (1024 * 1024)
    except Exception:
        free_mb = total_mb = -1
    snaps = sorted(_backups_dir().glob("snapshot_*.zip"))
    return {
        "data_dir": str(d),
        "nian_data_dir_env": os.environ.get("NIAN_DATA_DIR", ""),
        "exists": d.exists(),
        "users": len(users),
        "memorials": total_mem,
        "disk_free_mb": free_mb,
        "disk_total_mb": total_mb,
        "snapshots": [s.name for s in snaps],
        "data_loss_warning_present": (d / "DATA_LOSS_WARNING.txt").exists(),
    }


@router.post("/data/snapshot")
def make_snapshot(user=Depends(_require_owner)):
    """手动打一次快照。"""
    d = storage.DATA_DIR
    snap = _backups_dir() / f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}_manual.zip"
    n = 0
    with zipfile.ZipFile(snap, "w", zipfile.ZIP_DEFLATED) as z:
        for p in d.rglob("*"):
            if "_backups" in p.parts:
                continue
            if p.is_file():
                z.write(p, arcname=str(p.relative_to(d)))
                n += 1
    return {"name": snap.name, "files": n, "size_mb": snap.stat().st_size // (1024 * 1024)}


@router.get("/data/snapshot/{name}/download")
def download_snapshot(name: str, user=Depends(_require_owner)):
    """下载快照到本地保存（终极保险，下载到自己电脑）。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法名称")
    p = _backups_dir() / name
    if not p.exists():
        raise HTTPException(404, "快照不存在")
    return FileResponse(str(p), filename=name, media_type="application/zip")


@router.post("/data/snapshot/{name}/restore")
def restore_snapshot(name: str, user=Depends(_require_owner)):
    """从快照恢复数据（会先把当前数据另存为 _emergency_*.zip）。"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "非法名称")
    snap = _backups_dir() / name
    if not snap.exists():
        raise HTTPException(404, "快照不存在")
    d = storage.DATA_DIR
    # 先紧急备份当前
    emerg = _backups_dir() / f"emergency_before_restore_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(emerg, "w", zipfile.ZIP_DEFLATED) as z:
        for p in d.rglob("*"):
            if "_backups" in p.parts:
                continue
            if p.is_file():
                z.write(p, arcname=str(p.relative_to(d)))
    # 清空非备份目录的内容（保留 _backups）
    for child in d.iterdir():
        if child.name == "_backups":
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception:
            pass
    # 解压
    with zipfile.ZipFile(snap, "r") as z:
        z.extractall(str(d))
    return {"restored_from": name, "emergency_backup": emerg.name}
