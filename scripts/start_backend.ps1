# start_backend.ps1 — 一键启动后端 FastAPI 服务
# 用法（Windows PowerShell）：
#   .\start_backend.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = Split-Path -Parent $scriptDir
Set-Location $root

# 自动加载 .env（与 Streamlit 共用）
if (Test-Path ".env") {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([^#=\s][^=]*)=(.*)$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    Write-Host "[OK] 已加载 .env" -ForegroundColor Green
}

Write-Host "[INFO] 念念后端启动中（FastAPI）..." -ForegroundColor Cyan
Write-Host "       前端：http://localhost:8000/static/index.html" -ForegroundColor Cyan
Write-Host "       API 文档：http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "       健康检查：http://localhost:8000/api/health" -ForegroundColor Cyan
Write-Host ""

Set-Location "$root\backend"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
