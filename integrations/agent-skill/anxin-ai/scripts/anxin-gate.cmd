@echo off
where anxin-gate >nul 2>nul
if errorlevel 1 (
  echo {"schema_version":1,"status":"error","message":"未找到 anxin-gate。请先安装 anxinai-community[agent]。"}
  exit /b 2
)
anxin-gate %*

