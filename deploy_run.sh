#!/usr/bin/env bash
# ============================================================
# 旅途星辰 (TripStar) — 一键部署与启动脚本 (Linux / macOS)
# ============================================================
# 使用方法:
#   chmod +x deploy_run.sh
#   ./deploy_run.sh
# ============================================================

set -euo pipefail

# ---------- 颜色定义 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ============================================================
# 辅助函数
# ============================================================
info()    { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}○${NC} $1"; }
error()   { echo -e "${RED}✗${NC} $1"; }
title()   { echo -e "\n${BOLD}${CYAN}============================================${NC}"; echo -e "${BOLD}${CYAN}   $1${NC}"; echo -e "${BOLD}${CYAN}============================================${NC}\n"; }
step()    { echo -e "\n${BOLD}[$1]${NC} $2"; }

cleanup() {
    echo -e "\n${YELLOW}正在退出...${NC}"
    # 关闭后台进程
    if [ -n "${BACKEND_PID:-}" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "${FRONTEND_PID:-}" ]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

# ============================================================
# 1. 检查环境依赖
# ============================================================
check_env() {
    title "旅途星辰 TripStar — 部署与启动工具"

    # 检查 Python
    PYTHON_CMD=""
    if command -v python3 &>/dev/null; then
        PYTHON_VER="$(python3 --version 2>&1)"
        info "$PYTHON_VER"
        PYTHON_CMD="python3"
    elif command -v python &>/dev/null; then
        PYTHON_VER="$(python --version 2>&1)"
        info "$PYTHON_VER"
        PYTHON_CMD="python"
    else
        error "Python 未找到！请安装 Python 3.10+"
        return 1
    fi

    # 检查 Node.js
    if command -v node &>/dev/null; then
        NODE_VER="$(node --version 2>&1)"
        info "Node.js $NODE_VER"
    else
        error "Node.js 未找到！请安装 Node.js 18+"
        return 1
    fi

    # 检查 npm
    if command -v npm &>/dev/null; then
        NPM_VER="$(npm --version 2>&1)"
        info "npm v$NPM_VER"
    else
        error "npm 未找到！请安装 Node.js (包含 npm)"
        return 1
    fi

    # 检查 uv (可选)
    if command -v uv &>/dev/null; then
        info "uv 已安装 (将使用 uv 加速 Python 依赖安装)"
        USE_UV=true
    else
        warn "uv 未安装 (将使用 pip 安装，速度较慢)"
        warn "推荐安装 uv: pip install uv 或 curl -LsSf https://astral.sh/uv/install.sh | sh"
        USE_UV=false
    fi

    echo -e "\n${BOLD}项目目录: ${PROJECT_DIR}${NC}"
    return 0
}

# ============================================================
# 2. 主菜单
# ============================================================
main_menu() {
    title "主菜单"
    echo "  ${BOLD}1.${NC} 🚀  部署项目  (完整安装依赖与环境配置)"
    echo "  ${BOLD}2.${NC} ▶   启动项目  (检查部署状态并启动)"
    echo "  ${BOLD}3.${NC} ❌  退出"
    echo ""
    read -rp "请选择 [1/2/3]: " CHOICE

    case "$CHOICE" in
        1) deploy ;;
        2) start_project ;;
        3) cleanup ;;
        *) echo -e "${RED}无效选择，请重新输入${NC}" ; main_menu ;;
    esac
}

# ============================================================
# 3. 部署
# ============================================================
deploy() {
    title "🚀 开始部署项目"

    # ---------- 1. 后端 npm 安装 ----------
    step "1/4" "安装后端 Node.js 依赖 (小红书签名引擎)..."
    cd "$PROJECT_DIR/backend"
    if [ -d "node_modules" ]; then
        info "backend/node_modules 已存在，跳过"
    else
        echo -e "${YELLOW}  正在安装...${NC}"
        npm install
        info "后端 Node.js 依赖安装完成"
    fi

    # ---------- 2. Python 虚拟环境 ----------
    step "2/4" "创建 Python 虚拟环境并安装依赖..."
    cd "$PROJECT_DIR/backend"
    if [ -d ".venv" ]; then
        info "backend/.venv 已存在"
    else
        echo -e "${YELLOW}  创建虚拟环境...${NC}"
        if [ "$USE_UV" = true ]; then
            uv venv .venv
        else
            $PYTHON_CMD -m venv .venv
        fi
        info "虚拟环境创建成功"
    fi

    # 安装 Python 依赖
    echo -e "${YELLOW}  安装 Python 依赖包...${NC}"
    if [ "$USE_UV" = true ]; then
        .venv/bin/uv pip install -r requirements.txt
    else
        .venv/bin/pip install -r requirements.txt
    fi
    info "Python 依赖安装完成"

    # ---------- 3. 后端 .env ----------
    step "3/4" "配置后端环境变量文件..."
    cd "$PROJECT_DIR/backend"
    if [ -f ".env" ]; then
        info "backend/.env 已存在"
    else
        cp .env.example .env
        warn "已创建 backend/.env (请编辑填入你的 API Key)"
    fi

    # ---------- 4. 前端 ----------
    step "4/4" "安装前端依赖并配置环境变量..."
    cd "$PROJECT_DIR/frontend"
    if [ -d "node_modules" ]; then
        info "frontend/node_modules 已存在，跳过"
    else
        echo -e "${YELLOW}  安装前端依赖...${NC}"
        npm install
        info "前端依赖安装完成"
    fi

    if [ -f ".env" ]; then
        info "frontend/.env 已存在"
    else
        cp .env.example .env
        warn "已创建 frontend/.env (请编辑填入你的 API Key)"
    fi

    cd "$PROJECT_DIR"

    echo -e "\n${BOLD}${GREEN}============== ✅ 部署完成！ ==============${NC}"
    echo -e "\n${YELLOW}⚠  重要：请在启动前配置以下密钥文件：${NC}"
    echo ""
    echo "  ${BOLD}1.${NC} 编辑 ${CYAN}backend/.env${NC} — 填入你的 API Key："
    echo "     - LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_ID (必填)"
    echo "     - VITE_AMAP_WEB_KEY (必填)"
    echo "     - XHS_COOKIE (必填)"
    echo "     - GOOGLE_MAPS_API_KEY / GOOGLE_MAPS_PROXY (选填)"
    echo ""
    echo "  ${BOLD}2.${NC} 编辑 ${CYAN}frontend/.env${NC} — 填入你的 Key："
    echo "     - VITE_AMAP_WEB_KEY (与后端保持一致)"
    echo "     - VITE_AMAP_WEB_JS_KEY (Web端JS API类型Key)"
    echo ""
    echo "  ${BOLD}3.${NC} 编辑 ${CYAN}frontend/index.html${NC} — 填入 securityJsCode"
    echo ""
    echo -e "${BOLD}配置完成后，选择「启动项目」即可运行！${NC}"
    echo ""
    read -rp "按 Enter 返回主菜单..."
    main_menu
}

# ============================================================
# 4. 启动 — 检查部署状态
# ============================================================
start_project() {
    title "▶ 检查部署状态"

    DEPLOY_OK=true

    # 后端检查
    cd "$PROJECT_DIR/backend"
    if [ -d ".venv" ]; then
        info "后端虚拟环境  ✓"
    else
        error "后端虚拟环境 (.venv) 未找到"
        DEPLOY_OK=false
    fi

    if [ -d "node_modules" ]; then
        info "后端 Node.js 依赖  ✓"
    else
        error "后端 Node.js 依赖 (node_modules) 未找到"
        DEPLOY_OK=false
    fi

    if [ -f ".env" ]; then
        info "后端配置文件  ✓"
    else
        error "后端配置文件 (.env) 未找到"
        DEPLOY_OK=false
    fi

    # 前端检查
    cd "$PROJECT_DIR/frontend"
    if [ -d "node_modules" ]; then
        info "前端依赖  ✓"
    else
        error "前端依赖 (node_modules) 未找到"
        DEPLOY_OK=false
    fi

    if [ -f ".env" ]; then
        info "前端配置文件  ✓"
    else
        error "前端配置文件 (.env) 未找到"
        DEPLOY_OK=false
    fi

    cd "$PROJECT_DIR"

    echo ""
    if [ "$DEPLOY_OK" = false ]; then
        echo -e "${YELLOW}========================================${NC}"
        echo -e "${YELLOW}  项目尚未完成部署！${NC}"
        echo -e "${YELLOW}========================================${NC}"
        echo ""
        read -rp "$(echo -e "${BOLD}是否立即部署？ [y/N]: ${NC}")" DEPLOY_CHOICE
        if [[ "$DEPLOY_CHOICE" =~ ^[Yy]$ ]]; then
            deploy
            return
        else
            echo -e "${YELLOW}已取消，请先完成部署后再启动。${NC}"
            read -rp "按 Enter 返回主菜单..."
            main_menu
            return
        fi
    fi

    # ============================================================
    # 5. 启动服务
    # ============================================================
    start_services
}

start_services() {
    title "▶ 正在启动服务"

    # 获取配置的端口
    BACKEND_PORT=8000
    if [ -f "$PROJECT_DIR/backend/.env" ]; then
        ENV_PORT=$(grep -E '^PORT=' "$PROJECT_DIR/backend/.env" 2>/dev/null | cut -d= -f2)
        BACKEND_PORT="${ENV_PORT:-8000}"
    fi

    # 启动后端
    echo -e "${YELLOW}  启动后端服务 (端口: $BACKEND_PORT)...${NC}"
    cd "$PROJECT_DIR/backend"
    source .venv/bin/activate
    nohup uvicorn app.api.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload > "$PROJECT_DIR/backend.log" 2>&1 &
    BACKEND_PID=$!
    info "后端服务已启动 (PID: $BACKEND_PID)"

    # 等待后端就绪
    echo -e "${YELLOW}  等待后端服务就绪...${NC}"
    sleep 3

    # 启动前端
    echo -e "${YELLOW}  启动前端开发服务器...${NC}"
    cd "$PROJECT_DIR/frontend"
    nohup npm run dev > "$PROJECT_DIR/frontend.log" 2>&1 &
    FRONTEND_PID=$!
    info "前端服务已启动 (PID: $FRONTEND_PID)"

    cd "$PROJECT_DIR"

    echo -e "\n${BOLD}${GREEN}============== ✅ 启动成功！ ==============${NC}"
    echo ""
    echo -e "  前端地址: ${BOLD}http://localhost:5173${NC}"
    echo -e "  后端地址: ${BOLD}http://localhost:${BACKEND_PORT}${NC}"
    echo -e "  后端文档: ${BOLD}http://localhost:${BACKEND_PORT}/docs${NC}"
    echo ""
    echo -e "  ${YELLOW}后台日志:${NC}"
    echo -e "    后端: ${CYAN}tail -f backend.log${NC}"
    echo -e "    前端: ${CYAN}tail -f frontend.log${NC}"
    echo ""
    echo -e "  ${YELLOW}停止服务:${NC} ${CYAN}kill $BACKEND_PID $FRONTEND_PID${NC}"
    echo ""

    # 尝试自动打开浏览器
    case "$(uname -s)" in
        Linux*)  xdg-open http://localhost:5173 2>/dev/null || true ;;
        Darwin*) open http://localhost:5173 2>/dev/null || true ;;
    esac

    echo -e "${YELLOW}按 Ctrl+C 停止所有服务并退出${NC}"
    # 等待子进程
    wait
}

# ============================================================
# 入口
# ============================================================
if check_env; then
    main_menu
fi
