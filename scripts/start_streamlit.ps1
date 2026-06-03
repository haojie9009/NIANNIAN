# start_streamlit.ps1 — 一键启动 Streamlit（内部调试界面）
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$root = Split-Path -Parent $scriptDir
Set-Location $root

Write-Host "[INFO] 念念 Streamlit 启动中..." -ForegroundColor Cyan
Write-Host "       地址：http://localhost:8501" -ForegroundColor Cyan
Write-Host ""

streamlit run app.py --server.port 8501
