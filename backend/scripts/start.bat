@echo off
rem ============================================================
rem  企业资料管理系统 · 后端一键启动（Windows）
rem  用法：双击运行，或命令行执行 backend\scripts\start.bat
rem  前置：已安装依赖并配置好 backend\.env（见根 README §4）
rem ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."

rem 激活虚拟环境（若存在 .venv / venv）
if exist ".venv\Scripts\activate.bat" (
    echo [start] 激活虚拟环境 .venv
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    echo [start] 激活虚拟环境 venv
    call "venv\Scripts\activate.bat"
)

echo [start] 启动 FastAPI 后端 http://0.0.0.0:8000
echo [start] 健康检查: GET http://localhost:8000/api/health
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
    echo [start] 启动失败：请确认已安装依赖（pip install -r requirements.txt）
    pause
)
endlocal
