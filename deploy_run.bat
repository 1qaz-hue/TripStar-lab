@echo off
chcp 65001 >nul
title 旅途星辰 TripStar — 部署与启动工具
setlocal enabledelayedexpansion

:: ============================================================
:: 旅途星辰 (TripStar) — 一键部署与启动脚本 (Windows)
:: ============================================================

:: ---------- 颜色定义 ----------
set "ESC="
for /f "tokens=1,2 delims=#" %%a in ('"prompt #$H#$E# # & echo on & for %%b in (1) do rem"') do (
  set "ESC=%%b"
  goto :break_color
)
:break_color
set "GREEN=%ESC%[32m"
set "YELLOW=%ESC%[33m"
set "RED=%ESC%[31m"
set "CYAN=%ESC%[36m"
set "BOLD=%ESC%[1m"
set "NC=%ESC%[0m"

set "PROJECT_DIR=%CD%"

:: ============================================================
:: 检查环境依赖
:: ============================================================
:check_env
echo.
echo %BOLD%%CYAN%============================================%NC%
echo %BOLD%%CYAN%   旅途星辰 TripStar — 部署与启动工具%NC%
echo %BOLD%%CYAN%============================================%NC%
echo.

:: 检查 Python
set "PYTHON_CMD="
where python >nul 2>nul
if !errorlevel! equ 0 (
    for /f "delims=" %%i in ('python --version 2^>^&1') do set "PYTHON_VER=%%i"
    echo %GREEN%✓  %PYTHON_VER%%NC%
    set "PYTHON_CMD=python"
) else (
    where python3 >nul 2>nul
    if !errorlevel! equ 0 (
        for /f "delims=" %%i in ('python3 --version 2^>^&1') do set "PYTHON_VER=%%i"
        echo %GREEN%✓  %PYTHON_VER%%NC%
        set "PYTHON_CMD=python3"
    ) else (
        echo %RED%✗  Python 未找到！请安装 Python 3.10+%NC%
        echo %YELLOW%   下载: https://www.python.org/downloads/%NC%
        goto :end
    )
)

:: 检查 Node.js
where node >nul 2>nul
if !errorlevel! equ 0 (
    for /f "delims=" %%i in ('node --version') do set "NODE_VER=%%i"
    echo %GREEN%✓  Node.js %NODE_VER%%NC%
) else (
    echo %RED%✗  Node.js 未找到！请安装 Node.js 18+%NC%
    echo %YELLOW%   下载: https://nodejs.org/en/download/%NC%
    goto :end
)

:: 检查 npm
where npm >nul 2>nul
if !errorlevel! equ 0 (
    for /f "delims=" %%i in ('npm --version') do set "NPM_VER=%%i"
    echo %GREEN%✓  npm v%NPM_VER%%NC%
) else (
    echo %RED%✗  npm 未找到！请安装 Node.js (包含 npm)%NC%
    goto :end
)

:: 检查 uv (可选)
where uv >nul 2>nul
if !errorlevel! equ 0 (
    echo %GREEN%✓  uv 已安装 (将使用 uv 加速 Python 依赖安装)%NC%
    set "USE_UV=1"
) else (
    echo %YELLOW%○  uv 未安装 (将使用 pip 安装，速度较慢)%NC%
    echo %YELLOW%   推荐安装 uv: pip install uv 或 winget install astral-sh.uv%NC%
    set "USE_UV=0"
)

echo.
echo %BOLD%项目目录: %PROJECT_DIR%%NC%
echo.

:: ============================================================
:: 主菜单
:: ============================================================
:main_menu
echo %BOLD%%CYAN%============== 主菜单 ==============%NC%
echo.
echo  %BOLD%1.%NC% 🚀  部署项目  (完整安装依赖与环境配置)
echo  %BOLD%2.%NC% ▶   启动项目  (检查部署状态并启动)
echo  %BOLD%3.%NC% ❌  退出
echo.
set /p "CHOICE=请选择 [1/2/3]: "

if "!CHOICE!"=="1" goto :deploy
if "!CHOICE!"=="2" goto :start_project
if "!CHOICE!"=="3" goto :end
echo %RED%无效选择，请重新输入%NC%
goto :main_menu

:: ============================================================
:: 部署 — 自动安装所有依赖与配置
:: ============================================================
:deploy
echo.
echo %BOLD%%CYAN%============== 🚀 开始部署项目 ==============%NC%
echo.

:: ---------- 1. 后端 npm 安装 (小红书签名引擎) ----------
echo %BOLD%[1/4] 安装后端 Node.js 依赖 (小红书签名引擎)...%NC%
cd /d "%PROJECT_DIR%\backend"
if exist node_modules (
    echo %GREEN%  ✓  backend/node_modules 已存在，跳过%NC%
) else (
    echo %YELLOW%  正在安装...%NC%
    call npm install
    if !errorlevel! neq 0 (
        echo %RED%  ✗ npm install 失败！%NC%
        cd /d "%PROJECT_DIR%"
        goto :deploy_failed
    )
    echo %GREEN%  ✓ 后端 Node.js 依赖安装完成%NC%
)
echo.

:: ---------- 2. Python 虚拟环境 + 依赖 ----------
echo %BOLD%[2/4] 创建 Python 虚拟环境并安装依赖...%NC%
cd /d "%PROJECT_DIR%\backend"
if exist .venv (
    echo %GREEN%  ✓  backend/.venv 已存在%NC%
) else (
    echo %YELLOW%  创建虚拟环境...%NC%
    if "!USE_UV!"=="1" (
        call uv venv .venv
    ) else (
        "%PYTHON_CMD%" -m venv .venv
    )
    if !errorlevel! neq 0 (
        echo %RED%  ✗ 虚拟环境创建失败！%NC%
        cd /d "%PROJECT_DIR%"
        goto :deploy_failed
    )
    echo %GREEN%  ✓ 虚拟环境创建成功%NC%
)

:: 安装 Python 依赖
echo %YELLOW%  安装 Python 依赖包...%NC%
if "!USE_UV!"=="1" (
    call .venv\Scripts\uv pip install -r requirements.txt
) else (
    call .venv\Scripts\pip install -r requirements.txt
)
if !errorlevel! neq 0 (
    echo %RED%  ✗ Python 依赖安装失败！%NC%
    cd /d "%PROJECT_DIR%"
    goto :deploy_failed
)
echo %GREEN%  ✓ Python 依赖安装完成%NC%
echo.

:: ---------- 3. 后端 .env 配置 ----------
echo %BOLD%[3/4] 配置后端环境变量文件...%NC%
cd /d "%PROJECT_DIR%\backend"
if not exist .env (
    copy .env.example .env >nul
    echo %YELLOW%  ○ 已创建 backend\.env (请编辑填入你的 API Key)%NC%
) else (
    echo %GREEN%  ✓  backend\.env 已存在%NC%
)
echo.

:: ---------- 4. 前端 npm 安装 + .env ----------
echo %BOLD%[4/4] 安装前端依赖并配置环境变量...%NC%
cd /d "%PROJECT_DIR%\frontend"
if exist node_modules (
    echo %GREEN%  ✓  frontend/node_modules 已存在，跳过%NC%
) else (
    echo %YELLOW%  安装前端依赖 (npm install)...%NC%
    call npm install
    if !errorlevel! neq 0 (
        echo %RED%  ✗ npm install 失败！%NC%
        cd /d "%PROJECT_DIR%"
        goto :deploy_failed
    )
    echo %GREEN%  ✓ 前端依赖安装完成%NC%
)

if not exist .env (
    copy .env.example .env >nul
    echo %YELLOW%  ○ 已创建 frontend\.env (请编辑填入你的 API Key)%NC%
) else (
    echo %GREEN%  ✓  frontend\.env 已存在%NC%
)
cd /d "%PROJECT_DIR%"

echo.
echo %BOLD%%GREEN%============== ✅ 部署完成！ ==============%NC%
echo.
echo %YELLOW%⚠  重要：请在启动前配置以下密钥文件：%NC%
echo.
echo  %BOLD%1.%NC% 编辑 %CYAN%backend\.env%NC% — 填入你的 API Key：
echo    - LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID (必填)
echo    - VITE_AMAP_WEB_KEY (必填)
echo    - XHS_COOKIE (必填)
echo    - GOOGLE_MAPS_API_KEY / GOOGLE_MAPS_PROXY (选填)
echo.
echo  %BOLD%2.%NC% 编辑 %CYAN%frontend\.env%NC% — 填入你的 Key：
echo    - VITE_AMAP_WEB_KEY (与后端保持一致)
echo    - VITE_AMAP_WEB_JS_KEY (Web端JS API类型Key)
echo.
echo  %BOLD%3.%NC% 编辑 %CYAN%frontend\index.html%NC% — 填入 securityJsCode
echo.
echo %BOLD%配置完成后，选择「启动项目」即可运行！%NC%
echo.
pause
goto :main_menu

:deploy_failed
echo.
echo %RED%============== ❌ 部署失败 ==============%NC%
echo %YELLOW%请检查上面的错误信息，解决问题后重试。%NC%
pause
goto :main_menu

:: ============================================================
:: 启动 — 检查部署状态并启动项目
:: ============================================================
:start_project
echo.
echo %BOLD%%CYAN%============== ▶ 检查部署状态 ==============%NC%
echo.

set "DEPLOY_OK=1"

:: 检查后端部署状态
cd /d "%PROJECT_DIR%\backend"
if not exist .venv (
    echo %RED%  ✗ 后端虚拟环境 (.venv) 未找到%NC%
    set "DEPLOY_OK=0"
) else (
    echo %GREEN%  ✓ 后端虚拟环境  ✓%NC%
)

if not exist node_modules (
    echo %RED%  ✗ 后端 Node.js 依赖 (node_modules) 未找到%NC%
    set "DEPLOY_OK=0"
) else (
    echo %GREEN%  ✓ 后端 Node.js 依赖  ✓%NC%
)

if not exist .env (
    echo %RED%  ✗ 后端配置文件 (.env) 未找到%NC%
    set "DEPLOY_OK=0"
) else (
    echo %GREEN%  ✓ 后端配置文件  ✓%NC%
)

:: 检查前端部署状态
cd /d "%PROJECT_DIR%\frontend"
if not exist node_modules (
    echo %RED%  ✗ 前端依赖 (node_modules) 未找到%NC%
    set "DEPLOY_OK=0"
) else (
    echo %GREEN%  ✓ 前端依赖  ✓%NC%
)

if not exist .env (
    echo %RED%  ✗ 前端配置文件 (.env) 未找到%NC%
    set "DEPLOY_OK=0"
) else (
    echo %GREEN%  ✓ 前端配置文件  ✓%NC%
)

cd /d "%PROJECT_DIR%"

echo.
if "!DEPLOY_OK!"=="0" (
    echo %YELLOW%========================================%NC%
    echo %YELLOW%  项目尚未完成部署！%NC%
    echo %YELLOW%========================================%NC%
    echo.
    echo %BOLD%是否立即部署？%NC%
    set /p "DEPLOY_CHOICE=请输入 [y/N]: "
    if /i "!DEPLOY_CHOICE!"=="y" (
        goto :deploy
    ) else (
        echo %YELLOW%已取消，请先完成部署后再启动。%NC%
        pause
        goto :main_menu
    )
)

:: ============================================================
:: 启动前后端服务
:: ============================================================
:start_services
echo.
echo %BOLD%%CYAN%============== ▶ 正在启动服务 ==============%NC%
echo.

:: 获取后端端口
set "BACKEND_PORT=8000"
if exist "%PROJECT_DIR%\backend\.env" (
    for /f "tokens=2 delims==" %%a in ('type "%PROJECT_DIR%\backend\.env" ^| find "PORT="') do set "BACKEND_PORT=%%a"
)

:: 启动后端 (在新窗口中)
echo %YELLOW%  启动后端服务 (端口: %BACKEND_PORT%)...%NC%
start "TripStar-Backend" cmd /c "cd /d "%PROJECT_DIR%\backend" && call .venv\Scripts\activate && uvicorn app.api.main:app --host 0.0.0.0 --port %BACKEND_PORT% --reload"
echo %GREEN%  ✓ 后端服务已启动%NC%

:: 等待后端启动
echo %YELLOW%  等待后端服务就绪...%NC%
timeout /t 3 /nobreak >nul

:: 启动前端 (在新窗口中)
echo %YELLOW%  启动前端开发服务器...%NC%
start "TripStar-Frontend" cmd /c "cd /d "%PROJECT_DIR%\frontend" && npm run dev"
echo %GREEN%  ✓ 前端服务已启动%NC%

echo.
echo %BOLD%%GREEN%============== ✅ 启动成功！ ==============%NC%
echo.
echo  前端地址: %BOLD%http://localhost:5173%NC%
echo  后端地址: %BOLD%http://localhost:%BACKEND_PORT%%NC%
echo  后端文档: %BOLD%http://localhost:%BACKEND_PORT%/docs%NC%
echo.
echo %YELLOW%  提示:%NC%
echo  - 前后端各有一个独立命令行窗口，请勿关闭
echo  - 如需停止服务，直接关闭对应窗口即可
echo  - 浏览器将自动打开...
echo.

:: 尝试自动打开浏览器
start http://localhost:5173

pause
goto :main_menu

:: ============================================================
:: 结束
:: ============================================================
:end
echo.
echo %BOLD%%GREEN%感谢使用旅途星辰 TripStar！%NC%
echo.
pause
exit /b 0
