#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旅途星辰 (TripStar) — 一键部署与启动图形工具
双击运行即可，无需任何命令行操作。
"""

import os
import sys
import subprocess
import threading
import queue
import webbrowser
import time
import re
import json
import shutil
from pathlib import Path
from tkinter import (
    Tk, Frame, Button, Text, END, NORMAL, DISABLED,
    messagebox, scrolledtext, font, Label, Canvas,
    NW, BOTH, LEFT, RIGHT, X, Y, TOP, BOTTOM, SOLID, GROOVE
)
from datetime import datetime

# ────────── Windows 隐藏窗口工具 ──────────
if sys.platform == "win32":
    _HIDE = subprocess.CREATE_NO_WINDOW
else:
    _HIDE = 0

def _popen(cmd, **kwargs):
    """始终隐藏控制台窗口的 Popen"""
    kwargs.setdefault("creationflags", 0)
    kwargs["creationflags"] |= _HIDE
    return subprocess.Popen(cmd, **kwargs)

def _run(cmd, **kwargs):
    """始终隐藏控制台窗口的 subprocess.run"""
    kwargs.setdefault("creationflags", 0)
    kwargs["creationflags"] |= _HIDE
    return subprocess.run(cmd, **kwargs)

# ---------- 配置 ----------
PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"

# spinner 动画字符
SPINNER = ['◐', '◓', '◑', '◒']

# 各操作的步骤定义
DEPLOY_STEPS = [
    ("check_env",        "检查系统环境"),
    ("install_backend_npm", "安装后端 Node.js 依赖"),
    ("create_venv",      "创建 Python 虚拟环境"),
    ("install_python_deps", "安装 Python 依赖包"),
    ("config_backend_env",  "配置后端环境变量"),
    ("install_frontend_npm", "安装前端依赖"),
    ("config_frontend_env", "配置前端环境变量"),
]

START_STEPS = [
    ("check_backend",    "检查后端部署状态"),
    ("check_frontend",   "检查前端部署状态"),
    ("check_api_keys",   "检查 API Key 配置"),
    ("start_backend",    "启动后端服务"),
    ("start_frontend",   "启动前端服务"),
    ("open_browser",     "打开浏览器"),
]


# ============================================================
# 核心逻辑 — 步骤化的工作线程
# ============================================================
class DeployWorker:
    def __init__(self, step_callback, log_callback, ask_callback=None):
        """
        step_callback(action, step_id, desc, detail='')
          action: 'start' | 'success' | 'fail' | 'skip'
        log_callback(msg, level)
          level: 'info' | 'ok' | 'warn' | 'error'
        ask_callback(title, message) -> bool
          在后台线程中询问用户是/否，返回 True/False
        """
        self.step_cb = step_callback
        self.log_cb = log_callback
        self.ask_cb = ask_callback
        self._stop = threading.Event()
        self._fe_real_url = "http://localhost:5173"

    def stop(self):
        self._stop.set()

    # ---------- 工具 ----------
    def _run_cmd(self, cmd, cwd=None, timeout=300, shell=False):
        if self._stop.is_set():
            return False
        try:
            proc = _popen(
                cmd,
                cwd=str(cwd or PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=shell,
            )
            for line in iter(proc.stdout.readline, ""):
                if self._stop.is_set():
                    proc.kill()
                    return False
                line = line.rstrip()
                if line:
                    self.log_cb(line, "info")
            proc.wait(timeout=timeout)
            return proc.returncode == 0
        except Exception as e:
            self.log_cb(f"命令执行异常: {e}", "error")
            return False

    def _which(self, name):
        if sys.platform == "win32":
            r = _run(["where", name], capture_output=True, text=True)
        else:
            r = _run(["which", name], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    # ---------- 部署 ----------
    def deploy(self):
        # --- step 1: 检查环境 ---
        self.step_cb("start", "check_env", "检查系统环境")
        env_ok, python_cmd, use_uv = self._check_env_impl()
        if not env_ok:
            self.step_cb("fail", "check_env", "检查系统环境", "系统环境不满足要求")
            return False
        self.step_cb("success", "check_env", "检查系统环境")

        # --- step 2: 后端 npm ---
        self.step_cb("start", "install_backend_npm", "安装后端 Node.js 依赖")
        if (BACKEND_DIR / "node_modules").exists():
            self.log_cb("backend/node_modules 已存在，跳过", "ok")
            self.step_cb("skip", "install_backend_npm", "安装后端 Node.js 依赖", "已存在，跳过")
        else:
            self.log_cb("正在安装...", "info")
            npm = "npm.cmd" if sys.platform == "win32" else "npm"
            ok = self._run_cmd([npm, "install"], cwd=BACKEND_DIR, timeout=180)
            if not ok:
                self.log_cb("npm install 失败，正在重试（使用离线模式）...", "warn")
                ok = self._run_cmd([npm, "install", "--prefer-offline", "--no-audit", "--no-fund"], cwd=BACKEND_DIR, timeout=180)
            if not ok:
                self.step_cb("fail", "install_backend_npm", "安装后端 Node.js 依赖", "npm install 失败，请检查网络连接")
                return False
            self.step_cb("success", "install_backend_npm", "安装后端 Node.js 依赖")

        # --- step 3: 创建虚拟环境 ---
        self.step_cb("start", "create_venv", "创建 Python 虚拟环境")
        if (BACKEND_DIR / ".venv").exists():
            self.log_cb("backend/.venv 已存在，跳过", "ok")
            self.step_cb("skip", "create_venv", "创建 Python 虚拟环境", "已存在，跳过")
        else:
            self.log_cb("正在创建虚拟环境...", "info")
            # 多级修复链：uv → python -m venv → python -m venv --without-pip → virtualenv
            ok = False

            # 如存在损坏的 .venv 文件/目录，先清理
            venv_path = BACKEND_DIR / ".venv"
            if venv_path.exists():
                self.log_cb("  发现残留的 .venv，正在清理...", "warn")
                try:
                    if venv_path.is_dir():
                        shutil.rmtree(venv_path)
                    else:
                        venv_path.unlink()
                    self.log_cb("  已清理残留 .venv", "ok")
                except Exception as e:
                    self.log_cb(f"  清理残留失败: {e}", "warn")

            # 尝试 1: uv venv
            if use_uv:
                self.log_cb("  尝试方式 1/4: uv venv .venv", "info")
                ok = self._run_cmd(["uv", "venv", ".venv", "--clear"], cwd=BACKEND_DIR, timeout=120)

            # 尝试 2: python -m venv
            if not ok:
                self.log_cb("  尝试方式 2/4: python -m venv", "warn")
                ok = self._run_cmd([python_cmd, "-m", "venv", ".venv", "--clear"],
                                   cwd=BACKEND_DIR, timeout=120)

            # 尝试 3: python -m venv --without-pip（跳过 pip 引导，兼容 ensurepip 失败场景）
            if not ok:
                self.log_cb("  尝试方式 3/4: python -m venv --without-pip", "warn")
                ok = self._run_cmd([python_cmd, "-m", "venv", ".venv", "--clear", "--without-pip"],
                                   cwd=BACKEND_DIR, timeout=120)
                if ok:
                    # 手动安装 pip
                    self.log_cb("  venv 创建成功，正在安装 pip...", "info")
                    if sys.platform == "win32":
                        py_venv = str(venv_path / "Scripts" / "python.exe")
                    else:
                        py_venv = str(venv_path / "bin" / "python")
                    self._run_cmd([py_venv, "-m", "ensurepip", "--upgrade", "--default-pip"],
                                  cwd=BACKEND_DIR, timeout=120)

            # 尝试 4: virtualenv（先 pip 安装）
            if not ok:
                self.log_cb("  尝试方式 4/4: 安装 virtualenv 并创建", "warn")
                self._run_cmd([python_cmd, "-m", "pip", "install", "virtualenv", "--user"],
                              cwd=BACKEND_DIR, timeout=120)
                ok = self._run_cmd([python_cmd, "-m", "virtualenv", ".venv"],
                                   cwd=BACKEND_DIR, timeout=120)

            if not ok:
                # 输出详细诊断信息帮助用户排查
                self.log_cb("  ── 诊断信息 ──", "error")
                self._run_cmd([python_cmd, "--version"], cwd=BACKEND_DIR, timeout=10)
                self._run_cmd([python_cmd, "-c", "import venv; print('venv 模块:', '可用' if __import__('venv') else '不可用')"],
                              cwd=BACKEND_DIR, timeout=10)
                self.log_cb("  ── 建议手动操作 ──", "warn")
                self.log_cb("  请尝试在终端中执行:", "warn")
                self.log_cb(f"    cd {BACKEND_DIR}", "warn")
                self.log_cb("    python -m venv .venv", "warn")
                self.step_cb("fail", "create_venv", "创建 Python 虚拟环境",
                             "四种方式均失败，请参考日志中的手动操作指引")
                return False
            self.step_cb("success", "create_venv", "创建 Python 虚拟环境")

        # --- step 4: 安装 Python 依赖 ---
        self.step_cb("start", "install_python_deps", "安装 Python 依赖包")
        self.log_cb("正在安装 Python 依赖包（可能需要几分钟）...", "info")

        # 准备安装命令列表（按优先级从高到低）
        install_attempts = []
        if use_uv:
            install_attempts.append(["uv", "pip", "install", "-r", "requirements.txt"])
            install_attempts.append(["uv", "pip", "install", "--no-build-isolation", "-r", "requirements.txt"])
        # pip 兜底
        if sys.platform == "win32":
            pip_exe = str(BACKEND_DIR / ".venv" / "Scripts" / "pip.exe")
        else:
            pip_exe = str(BACKEND_DIR / ".venv" / "bin" / "pip")
        install_attempts.append([pip_exe, "install", "-r", "requirements.txt"])
        install_attempts.append([pip_exe, "install", "--no-build-isolation", "-r", "requirements.txt"])

        ok = False
        for i, cmd in enumerate(install_attempts):
            if i > 0:
                self.log_cb(f"重试方式 {i + 1}/{len(install_attempts)}...", "warn")
            if self._run_cmd(cmd, cwd=BACKEND_DIR, timeout=600):
                ok = True
                break

        if not ok:
            self.step_cb("fail", "install_python_deps", "安装 Python 依赖包", "所有安装方式均失败，见日志")
            return False
        self.step_cb("success", "install_python_deps", "安装 Python 依赖包")

        # --- step 5: 后端 .env ---
        self.step_cb("start", "config_backend_env", "配置后端环境变量")
        env_file = BACKEND_DIR / ".env"
        example = BACKEND_DIR / ".env.example"
        if env_file.exists():
            self.log_cb("backend/.env 已存在", "ok")
            self.step_cb("skip", "config_backend_env", "配置后端环境变量", "已存在")
        elif example.exists():
            env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            self.log_cb("已创建 backend/.env，请编辑填入 API Key", "warn")
            self.step_cb("success", "config_backend_env", "配置后端环境变量", "请编辑填入 API Key")
        else:
            self.step_cb("fail", "config_backend_env", "配置后端环境变量", ".env.example 未找到")
            return False

        # --- step 6: 前端 npm ---
        self.step_cb("start", "install_frontend_npm", "安装前端依赖")
        if (FRONTEND_DIR / "node_modules").exists():
            self.log_cb("frontend/node_modules 已存在，跳过", "ok")
            self.step_cb("skip", "install_frontend_npm", "安装前端依赖", "已存在，跳过")
        else:
            self.log_cb("正在安装前端依赖...", "info")
            npm = "npm.cmd" if sys.platform == "win32" else "npm"
            ok = self._run_cmd([npm, "install"], cwd=FRONTEND_DIR, timeout=180)
            if not ok:
                self.log_cb("npm install 失败，正在重试（使用离线模式）...", "warn")
                ok = self._run_cmd([npm, "install", "--prefer-offline", "--no-audit", "--no-fund"], cwd=FRONTEND_DIR, timeout=180)
            if not ok:
                self.step_cb("fail", "install_frontend_npm", "安装前端依赖", "npm install 失败，请检查网络连接")
                return False
            self.step_cb("success", "install_frontend_npm", "安装前端依赖")

        # --- step 7: 前端 .env ---
        self.step_cb("start", "config_frontend_env", "配置前端环境变量")
        front_env = FRONTEND_DIR / ".env"
        front_example = FRONTEND_DIR / ".env.example"
        if front_env.exists():
            self.log_cb("frontend/.env 已存在", "ok")
            self.step_cb("skip", "config_frontend_env", "配置前端环境变量", "已存在")
        elif front_example.exists():
            front_env.write_text(front_example.read_text(encoding="utf-8"), encoding="utf-8")
            self.log_cb("已创建 frontend/.env，请编辑填入 API Key", "warn")
            self.step_cb("success", "config_frontend_env", "配置前端环境变量", "请编辑填入 API Key")
        else:
            self.step_cb("fail", "config_frontend_env", "配置前端环境变量", ".env.example 未找到")
            return False

        self.log_cb("", "info")
        self.log_cb("══════ 部署完成！请配置 API Key 后启动 ══════", "ok")
        return True

    # ---------- 启动 ----------
    def start(self):
        # --- step 1: 检查后端 ---
        self.step_cb("start", "check_backend", "检查后端部署状态")
        backend_ok = True
        checks_b = [
            (BACKEND_DIR / ".venv",         "后端虚拟环境 (.venv)"),
            (BACKEND_DIR / "node_modules",   "后端 Node.js 依赖"),
            (BACKEND_DIR / ".env",           "后端配置文件"),
        ]
        for path, desc in checks_b:
            if path.exists():
                self.log_cb(f"  ✓ {desc}", "ok")
            else:
                self.log_cb(f"  ✗ {desc} 未找到", "error")
                backend_ok = False
        if backend_ok:
            self.step_cb("success", "check_backend", "检查后端部署状态")
        else:
            detail = self._missing_hint([p for p, _ in checks_b])
            self.step_cb("fail", "check_backend", "检查后端部署状态", detail)
            return "need_deploy"

        # --- step 2: 检查前端 ---
        self.step_cb("start", "check_frontend", "检查前端部署状态")
        front_ok = True
        checks_f = [
            (FRONTEND_DIR / "node_modules",  "前端依赖"),
            (FRONTEND_DIR / ".env",          "前端配置文件"),
        ]
        for path, desc in checks_f:
            if path.exists():
                self.log_cb(f"  ✓ {desc}", "ok")
            else:
                self.log_cb(f"  ✗ {desc} 未找到", "error")
                front_ok = False
        if front_ok:
            self.step_cb("success", "check_frontend", "检查前端部署状态")
        else:
            detail = self._missing_hint([p for p, _ in checks_f])
            self.step_cb("fail", "check_frontend", "检查前端部署状态", detail)
            return "need_deploy"

        # --- step 3: 检查 API Key ---
        self.step_cb("start", "check_api_keys", "检查 API Key 配置")
        key_ok = self._check_env_keys_impl()
        if key_ok:
            self.step_cb("success", "check_api_keys", "检查 API Key 配置")
        else:
            self.step_cb("fail", "check_api_keys", "检查 API Key 配置",
                         "部分 Key 未配置，部分功能可能不可用")

        # --- step 4: 启动后端 (带健康检查) ---
        self.step_cb("start", "start_backend", "启动后端服务")
        port = self._get_backend_port()
        self.log_cb(f"后端端口: {port}", "info")

        # 检查端口是否被占用
        port_status = self._check_port_or_ask(port, "后端")
        if port_status == "cancelled":
            self.step_cb("fail", "start_backend", "启动后端服务", "端口被占用，用户取消")
            return False
        be_skip = port_status == "ours"  # 自己的进程，跳过启动直接验证

        if sys.platform == "win32":
            venv_python = str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe")
        else:
            venv_python = str(BACKEND_DIR / ".venv" / "bin" / "python")
        backend_cmd = [
            venv_python, "-m", "uvicorn", "app.api.main:app",
            "--host", "0.0.0.0", "--port", str(port), "--reload",
        ]

        backend_url = f"http://127.0.0.1:{port}/docs"   # 127.0.0.1 避免 IPv6 解析问题

        if be_skip:
            # 本项目自己的进程已在运行，直接验证
            if self._health_check(backend_url):
                self.log_cb("后端已在运行中 ✓", "ok")
                self.step_cb("success", "start_backend", "启动后端服务")
            else:
                self.log_cb("后端进程存在但无法访问，尝试重新启动...", "warn")
                be_skip = False  # 降级为重新启动

        if not be_skip:
            # 后端输出写入日志文件，便于排查
            backend_log = PROJECT_DIR / "backend_startup.log"
            kwargs = {"cwd": str(BACKEND_DIR),
                      "stdout": open(backend_log, "w", encoding="utf-8"),
                      "stderr": subprocess.STDOUT}
            self._backend_proc = None
            try:
                self._backend_proc = _popen(backend_cmd, **kwargs)
                self.log_cb("后端进程已启动，等待就绪...", "info")

                # 健康检查：最多等 15 秒
                for i in range(15):
                    time.sleep(1)
                    if self._health_check(backend_url):
                        self.log_cb(f"后端服务已就绪 ✓ (耗时 {i+1}s)", "ok")
                        self.step_cb("success", "start_backend", "启动后端服务")
                        break
                else:
                    # 超时 — 检查进程是否已崩溃
                    exit_code = self._backend_proc.poll()
                    if exit_code is not None:
                        self.log_cb(f"后端进程已退出 (退出码: {exit_code})", "error")
                        self._show_log_tail(backend_log, "后端启动日志")
                    else:
                        self.log_cb("后端进程运行中但无法访问，可能启动较慢", "warn")
                        self.log_cb("请稍后手动刷新页面", "warn")
                    self.step_cb("fail", "start_backend", "启动后端服务", "服务未就绪")
                    return False
            except Exception as e:
                self.log_cb(f"后端启动失败: {e}，正在重试...", "warn")
                try:
                    self._backend_proc = _popen(backend_cmd, **kwargs)
                    time.sleep(5)
                    if self._health_check(backend_url):
                        self.log_cb("后端服务已就绪（重试成功）", "ok")
                        self.step_cb("success", "start_backend", "启动后端服务")
                    else:
                        self.log_cb("后端服务重试后仍未就绪", "error")
                        self.step_cb("fail", "start_backend", "启动后端服务", str(e))
                        return False
                except Exception as e2:
                    self.log_cb(f"后端启动重试仍然失败: {e2}", "error")
                    self.step_cb("fail", "start_backend", "启动后端服务", str(e2))
                    return False

        # --- step 5: 启动前端 (带健康检查) ---
        self.step_cb("start", "start_frontend", "启动前端服务")

        # 检查前端端口 5173 是否被占用
        fe_status = self._check_port_or_ask(5173, "前端", prompt_ours=True)
        if fe_status == "cancelled":
            self.step_cb("fail", "start_frontend", "启动前端服务", "端口被占用，用户取消")
            return False

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        frontend_url = "http://127.0.0.1:5173"   # 健康检查用 127.0.0.1 避免 IPv6 问题

        fe_skip = fe_status == "ours"

        if fe_skip:
            if self._health_check(frontend_url):
                self.log_cb("前端已在运行中 ✓", "ok")
                self.step_cb("success", "start_frontend", "启动前端服务")
            else:
                self.log_cb("前端进程存在但无法访问，尝试重新启动...", "warn")
                fe_skip = False

        if not fe_skip:
            # 前端输出写入日志文件
            frontend_log = PROJECT_DIR / "frontend_startup.log"
            # 禁用颜色参数，防止 Node.js v24+ 自动传入 --color
            fe_env = os.environ.copy()
            fe_env["NO_COLOR"] = "1"
            fe_env["FORCE_COLOR"] = "0"
            kwargs = {"cwd": str(FRONTEND_DIR),
                      "stdout": open(frontend_log, "w", encoding="utf-8"),
                      "stderr": subprocess.STDOUT,
                      "env": fe_env}
            self._frontend_proc = None
            try:
                self._frontend_proc = _popen([npm_cmd, "run", "dev"], **kwargs)
                self.log_cb("前端进程已启动，等待就绪...", "info")

                # 健康检查：最多等 20 秒 (Vite 首次启动较慢)
                for i in range(20):
                    time.sleep(1)
                    if self._health_check(frontend_url, timeout=2):
                        self.log_cb(f"前端服务已就绪 ✓ (耗时 {i+1}s)", "ok")
                        self.step_cb("success", "start_frontend", "启动前端服务")
                        break
                else:
                    exit_code = self._frontend_proc.poll()
                    if exit_code is not None:
                        self.log_cb(f"前端进程已退出 (退出码: {exit_code})", "error")
                        self._show_log_tail(frontend_log, "前端启动日志")
                        self.step_cb("fail", "start_frontend", "启动前端服务", "Vite 已退出")
                        return False
                    else:
                        # 从 Vite 日志中解析实际运行的端口
                        self.log_cb("正在从启动日志中解析端口...", "info")
                        real_url = self._parse_vite_url(frontend_log)
                        if real_url:
                            self.log_cb(f"检测到 Vite 实际运行地址: {real_url}", "ok")
                            if self._health_check(real_url, timeout=2):
                                self.log_cb(f"前端服务已就绪 ✓", "ok")
                                # 更新前端 URL 供后续步骤使用
                                self._fe_real_url = real_url
                                self.step_cb("success", "start_frontend", "启动前端服务")
                            else:
                                self.log_cb(f"Vite 日志显示 {real_url} 但无法访问", "error")
                                self._show_log_tail(frontend_log, "Vite 启动日志")
                                self.step_cb("fail", "start_frontend", "启动前端服务",
                                             f"Vite 在 {real_url} 但无法访问")
                                return False
                        else:
                            self._show_log_tail(frontend_log, "Vite 启动日志")
                            self.step_cb("fail", "start_frontend", "启动前端服务",
                                         "Vite 未就绪")
                            return False
            except Exception as e:
                self.log_cb(f"前端启动失败: {e}，正在重试...", "warn")
                try:
                    self._frontend_proc = _popen([npm_cmd, "run", "dev"], **kwargs)
                    time.sleep(10)
                    if self._health_check(frontend_url):
                        self.log_cb("前端服务已就绪（重试成功）", "ok")
                        self.step_cb("success", "start_frontend", "启动前端服务")
                    else:
                        self.log_cb("前端服务重试后仍未就绪", "error")
                        self.step_cb("fail", "start_frontend", "启动前端服务", str(e))
                        return False
                except Exception as e2:
                    self.log_cb(f"前端启动重试仍然失败: {e2}", "error")
                    self.step_cb("fail", "start_frontend", "启动前端服务", str(e2))
                    return False

        # --- step 6: 记下端口，由 GUI 询问用户后决定是否打开浏览器 ---
        self.step_cb("start", "open_browser", "打开浏览器")
        fe_url = getattr(self, "_fe_real_url", "http://localhost:5173")
        self.log_cb(f"前端地址: {fe_url}", "ok")
        self.log_cb(f"后端文档: http://localhost:{port}/docs", "ok")
        # 把前端实际 URL 带回 GUI
        return ("started", port, fe_url)

    # ---------- 内部辅助 ----------
    def _check_env_impl(self):
        self.log_cb("检查 Python...", "info")
        python_cmd = None
        for cmd in ("python3", "python"):
            path = self._which(cmd)
            if path:
                try:
                    ver = _run([cmd, "--version"], capture_output=True, text=True, timeout=10)
                    self.log_cb(f"  ✓ {ver.stdout.strip() or ver.stderr.strip()}", "ok")
                    python_cmd = cmd
                    break
                except Exception:
                    continue
        if not python_cmd:
            self.log_cb("  ✗ Python 未找到！请安装 Python 3.10+", "error")
            return False, None, False

        self.log_cb("检查 Node.js...", "info")
        if self._which("node"):
            ver = _run(["node", "--version"], capture_output=True, text=True, timeout=10)
            self.log_cb(f"  ✓ Node.js {ver.stdout.strip()}", "ok")
        else:
            self.log_cb("  ✗ Node.js 未找到！请安装 Node.js 18+", "error")
            return False, None, False

        use_uv = self._which("uv") is not None
        if use_uv:
            self.log_cb("  ✓ uv 已安装（将加速 Python 依赖安装）", "ok")
        else:
            # 多方式安装 uv：pip → 独立安装器
            self.log_cb("  ○ uv 未安装，正在自动安装...", "warn")

            # 方式 1: pip install uv
            ok = self._run_cmd([python_cmd, "-m", "pip", "install", "uv", "--quiet"],
                               cwd=BACKEND_DIR, timeout=120)

            # 方式 2: pip install uv --user（用户作用域）
            if not ok:
                self.log_cb("  重试: pip install uv --user", "warn")
                ok = self._run_cmd([python_cmd, "-m", "pip", "install", "uv", "--user", "--quiet"],
                                   cwd=BACKEND_DIR, timeout=120)

            # 方式 3: 指定 PyPI 镜像源
            if not ok:
                self.log_cb("  重试: 指定 PyPI 源安装", "warn")
                ok = self._run_cmd(
                    [python_cmd, "-m", "pip", "install", "uv", "--quiet",
                     "-i", "https://pypi.org/simple/"],
                    cwd=BACKEND_DIR, timeout=120,
                )

            # 方式 4 (Windows): 独立 PowerShell 安装器
            if not ok and sys.platform == "win32":
                self.log_cb("  重试: 独立安装器 (PowerShell)", "warn")
                ps_cmd = (
                    'powershell -ExecutionPolicy ByPass -c '
                    '"irm https://astral.sh/uv/install.ps1 | iex"'
                )
                ok = self._run_cmd(ps_cmd, cwd=PROJECT_DIR, timeout=120, shell=True)

            # 方式 4 (Linux/macOS): curl 安装器
            if not ok and sys.platform != "win32":
                self.log_cb("  重试: 独立安装器 (curl)", "warn")
                ok = self._run_cmd(
                    'curl -LsSf https://astral.sh/uv/install.sh | sh',
                    cwd=PROJECT_DIR, timeout=120, shell=True,
                )

            if ok:
                # 重新检测 uv
                if self._which("uv"):
                    self.log_cb("  ✓ uv 安装成功", "ok")
                    use_uv = True
                else:
                    self.log_cb("  ○ uv 已安装但不在 PATH 中（将使用 pip）", "warn")
            else:
                self.log_cb("  ○ uv 自动安装均失败（将使用 pip 作为备选）", "warn")

        return True, python_cmd, use_uv

    def _check_env_keys_impl(self):
        keys_to_check = [
            ("LLM_API_KEY",          BACKEND_DIR / ".env"),
            ("VITE_AMAP_WEB_KEY",   BACKEND_DIR / ".env"),
            ("XHS_COOKIE",          BACKEND_DIR / ".env"),
            ("VITE_AMAP_WEB_JS_KEY", FRONTEND_DIR / ".env"),
        ]
        all_ok = True
        placeholders = ["your_key", "your_url", "your_model", "your_amap",
                        "your_xhs", "your_", "key_here"]
        for key_name, env_file in keys_to_check:
            if not env_file.exists():
                self.log_cb(f"  ○ {env_file.name} 不存在，跳过 Key 检查", "warn")
                continue
            try:
                content = env_file.read_text(encoding="utf-8")
                match = re.search(rf"^{re.escape(key_name)}=(.+)$", content, re.MULTILINE)
                if match:
                    value = match.group(1).strip().strip('"').strip("'")
                    is_ph = any(p in value.lower() for p in placeholders)
                    is_empty = not value
                    if is_empty or is_ph:
                        self.log_cb(f"  ○ {key_name} 未配置（仍为占位符）", "warn")
                        all_ok = False
                    else:
                        masked = value[:10] + "..." if len(value) > 10 else value
                        self.log_cb(f"  ✓ {key_name} 已配置", "ok")
                else:
                    self.log_cb(f"  ○ {key_name} 未在 .env 中找到", "warn")
                    all_ok = False
            except Exception:
                pass
        return all_ok

    def _get_backend_port(self):
        env_file = BACKEND_DIR / ".env"
        if env_file.exists():
            try:
                m = re.search(r"^PORT=(\d+)", env_file.read_text(encoding="utf-8"), re.MULTILINE)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
        return 8000

    def _find_process_by_port(self, port):
        """查找占用端口的进程，返回 (pid, 名称, 是否为本项目进程) 或 None"""
        try:
            if sys.platform == "win32":
                r = _run(
                    ["netstat", "-ano", "-p", "TCP"],
                    capture_output=True, text=True, timeout=15
                )
                for line in r.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and f":{port}" in parts[1] and "LISTENING" in parts[3]:
                        pid = parts[4]
                        # 获取进程名称
                        tr = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                            capture_output=True, text=True, timeout=10)
                        pname = "未知"
                        for tl in tr.stdout.splitlines():
                            tl = tl.strip()
                            if tl and tl.lower() != "info:":
                                pname = tl.split()[0] if tl.split() else "未知"
                        # 判断是否为本项目进程
                        is_ours = self._is_our_process(pid)
                        return (pid, pname, is_ours)
            else:
                r = _run(
                    ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-P", "-n"],
                    capture_output=True, text=True, timeout=15
                )
                for line in r.stdout.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 2:
                        pid = parts[1]
                        pname = parts[0]
                        is_ours = self._is_our_process(pid)
                        return (pid, pname, is_ours)
        except Exception:
            pass
        return None

    def _is_our_process(self, pid):
        """检查进程是否为本项目启动的服务（根据命令行路径判断）"""
        try:
            cmdline = ""
            if sys.platform == "win32":
                r = _run(
                    ["wmic", "process", "where", f"processid={pid}", "get", "commandline", "/format:list"],
                    capture_output=True, text=True, timeout=10
                )
                for line in r.stdout.splitlines():
                    if line.startswith("CommandLine="):
                        cmdline = line[12:]
                        break
            else:
                r = _run(
                    ["ps", "-p", str(pid), "-o", "args="],
                    capture_output=True, text=True, timeout=10
                )
                cmdline = r.stdout.strip()
            # 检查命令行是否包含本项目路径
            proj_str = str(PROJECT_DIR).lower()
            return proj_str in cmdline.lower() if cmdline else False
        except Exception:
            return False

    def _kill_port_process(self, port, pid):
        """杀掉占用端口的进程"""
        self.log_cb(f"正在终止进程 (PID: {pid})...", "warn")
        try:
            if sys.platform == "win32":
                r = _run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, text=True, timeout=10)
            else:
                r = _run(["kill", "-9", str(pid)],
                                   capture_output=True, text=True, timeout=10)
            ok = r.returncode == 0
            if ok:
                self.log_cb(f"  已终止进程 (PID: {pid})", "ok")
                time.sleep(1)
            else:
                self.log_cb(f"  终止失败: {r.stderr.strip()}", "error")
            return ok
        except Exception as e:
            self.log_cb(f"  终止进程异常: {e}", "error")
            return False

    def _check_port_or_ask(self, port, label="服务", prompt_ours=False):
        """检查端口是否被占用
           prompt_ours=True 时，即使本项目的老进程也弹窗询问（防止旧进程占端口）
        返回: 'free' 空闲 | 'ours' 本项目进程 | 'killed' 已杀掉 | 'cancelled' 用户取消
        """
        info = self._find_process_by_port(port)
        if info is None:
            return "free"
        pid, pname, is_ours = info

        if is_ours and not prompt_ours:
            self.log_cb(f"  ✓ 端口 {port} 已被本项目占用（跳过启动）", "ok")
            return "ours"

        self.log_cb(f"⚠ 端口 {port} 已被 {pname} (PID: {pid}) 占用", "warn")
        if self.ask_cb:
            msg = (
                f"端口 {port} 已被占用\n\n"
                f"占用程序: {pname}\n"
                f"进程 PID: {pid}\n\n"
                f"是否终止该进程以释放端口？"
            )
            if self.ask_cb("端口被占用", msg):
                return "killed" if self._kill_port_process(port, pid) else "cancelled"
            self.log_cb(f"用户取消 — {label}启动终止", "warn")
            return "cancelled"
        return "free"

    def _missing_hint(self, paths):
        missing = [p.name for p in paths if not p.exists()]
        return f"缺失: {', '.join(missing)}" if missing else "请先部署项目"

    def _health_check(self, url, timeout=2):
        """检测服务是否可访问 — 先 TCP 端口检测，失败后回退 HTTP"""
        import urllib.request
        # 从 URL 提取 host:port
        m = re.match(r'https?://([^:/]+)(?::(\d+))?', url)
        if m:
            host = m.group(1)
            port = int(m.group(2)) if m.group(2) else (443 if url.startswith("https") else 80)
            import socket
            try:
                s = socket.create_connection((host, port), timeout=timeout)
                s.close()
                return True
            except Exception:
                pass  # TCP 失败，尝试 HTTP
        # HTTP 回退
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_vite_url(log_path):
        """从 Vite 启动日志中解析实际地址（端口被占用时 Vite 会递增端口）"""
        try:
            text = Path(log_path).read_text(encoding="utf-8")
            # 去除 ANSI 转义码 \x1b[...m
            clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
            # 再尝试移除日志中残留的 [NNm 格式码
            clean = re.sub(r'\[[0-9;]*m', '', clean)
            # 匹配 Local: 地址行
            m = re.search(r'Local:\s*(https?://[^\s]+)', clean)
            if m:
                return m.group(1).rstrip("/")
            # 备选：匹配 localhost:端口 模式
            m = re.search(r'localhost:(\d{4,5})', clean)
            if m:
                return f"http://localhost:{m.group(1)}"
        except Exception:
            pass
        return None

    def _show_log_tail(self, log_path, label="日志"):
        """显示日志文件尾部内容（用于诊断启动失败）"""
        path = Path(log_path)
        if not path.exists():
            self.log_cb(f"  {label}文件不存在", "warn")
            return
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            # 只显示最后 10 行
            tail = lines[-10:] if len(lines) > 10 else lines
            if not tail:
                self.log_cb(f"  {label}: (无内容)", "warn")
                return
            self.log_cb(f"  ── {label} (最后 {len(tail)} 行) ──", "error")
            for line in tail:
                self.log_cb(f"  | {line}", "error")
        except Exception as e:
            self.log_cb(f"  读取{label}失败: {e}", "warn")


# ============================================================
# GUI 组件 — 步骤指示面板
# ============================================================
class StepPanel(Frame):
    """带圆点指示器的步骤列表组件"""
    COLORS = {
        "pending": "#555",
        "running": "#4fc3f7",
        "success": "#4caf50",
        "fail":    "#f44336",
        "skip":    "#888",
        "bg":      "#12121f",
        "text":    "#ccc",
        "text_light": "#777",
    }

    def __init__(self, master, steps, **kwargs):
        super().__init__(master, bg=self.COLORS["bg"], **kwargs)
        self._steps = steps          # [(id, desc), ...]
        self._indicators = {}        # step_id -> StringVar (for spinner)
        self._status = {}            # step_id -> 'pending'|'running'|'success'|'fail'|'skip'
        self._desc_labels = {}       # step_id -> Label
        self._detail_labels = {}     # step_id -> Label
        self._spinner_jobs = {}      # step_id -> after job id
        self._spinner_idx = {}       # step_id -> current index
        self._rows = {}              # step_id -> Frame

        self._build()

    def _build(self):
        for i, (step_id, desc) in enumerate(self._steps):
            row = Frame(self, bg=self.COLORS["bg"])
            row.pack(fill=X, pady=(0 if i == 0 else 2, 0))
            self._rows[step_id] = row

            # 圆点指示器 Label
            indicator = Label(row, text="○", font=("Consolas", 14),
                              fg=self.COLORS["pending"], bg=self.COLORS["bg"],
                              width=2, anchor="center")
            indicator.pack(side=LEFT, padx=(8, 4))
            self._indicators[step_id] = indicator

            # 步骤描述（文本可能变化）
            desc_label = Label(row, text=desc, font=("Microsoft YaHei", 10),
                               fg=self.COLORS["text"], bg=self.COLORS["bg"],
                               anchor="w")
            desc_label.pack(side=LEFT, fill=X, expand=True)
            self._desc_labels[step_id] = desc_label

            # 详情文字（右侧，用于显示小提示）
            detail_label = Label(row, text="", font=("Microsoft YaHei", 8),
                                 fg=self.COLORS["text_light"], bg=self.COLORS["bg"],
                                 anchor="e")
            detail_label.pack(side=RIGHT, padx=(4, 8))
            self._detail_labels[step_id] = detail_label

            self._status[step_id] = "pending"
            self._spinner_idx[step_id] = 0

    def set_step(self, step_id, status, detail=""):
        """更新某个步骤的状态和详情文字"""
        if step_id not in self._indicators:
            return

        # 停止旧的 spinner
        if step_id in self._spinner_jobs and self._spinner_jobs[step_id]:
            try:
                self.after_cancel(self._spinner_jobs[step_id])
            except Exception:
                pass
            self._spinner_jobs[step_id] = None

        indicator = self._indicators[step_id]
        desc_label = self._desc_labels[step_id]
        detail_label = self._detail_labels[step_id]
        self._status[step_id] = status

        color_map = {
            "pending": self.COLORS["pending"],
            "running": self.COLORS["running"],
            "success": self.COLORS["success"],
            "fail":    self.COLORS["fail"],
            "skip":    self.COLORS["skip"],
        }
        char_map = {
            "pending": "○",
            "running": "◌",
            "success": "●",
            "fail":    "●",
            "skip":    "○",
        }
        color = color_map.get(status, self.COLORS["pending"])
        indicator.config(text=char_map.get(status, "○"), fg=color)

        if status == "success":
            desc_label.config(fg=self.COLORS["success"])
            indicator.config(text="✓", fg=self.COLORS["success"])
        elif status == "fail":
            desc_label.config(fg=self.COLORS["fail"])
            indicator.config(text="✗", fg=self.COLORS["fail"])
        elif status == "skip":
            indicator.config(text="→", fg=self.COLORS["skip"])
            desc_label.config(fg=self.COLORS["text"])
        elif status == "running":
            desc_label.config(fg=self.COLORS["running"])
            # 启动 spinner
            self._spinner_idx[step_id] = 0
            self._animate_spinner(step_id)
        else:  # pending
            desc_label.config(fg=self.COLORS["text"])

        if detail:
            detail_label.config(text=detail)
        else:
            detail_label.config(text="")

    def _animate_spinner(self, step_id):
        """spinner 动画帧"""
        idx = self._spinner_idx.get(step_id, 0)
        char = SPINNER[idx % len(SPINNER)]
        indicator = self._indicators.get(step_id)
        if indicator:
            indicator.config(text=char, fg=self.COLORS["running"])
        self._spinner_idx[step_id] = idx + 1
        # 如果状态仍然是 running，继续动画
        if self._status.get(step_id) == "running":
            self._spinner_jobs[step_id] = self.after(120, self._animate_spinner, step_id)

    def reset_all(self):
        """将所有步骤重置为 pending"""
        for step_id in self._indicators:
            # 停止 spinner
            if step_id in self._spinner_jobs and self._spinner_jobs[step_id]:
                try:
                    self.after_cancel(self._spinner_jobs[step_id])
                except Exception:
                    pass
                self._spinner_jobs[step_id] = None
            self.set_step(step_id, "pending")


# ============================================================
# GUI 主界面
# ============================================================
class DeployGUI:
    COLORS = {
        "bg":        "#0f0f1a",
        "panel_bg":  "#1a1a2e",
        "accent":    "#16213e",
        "text":      "#e0e0e0",
        "text_dim":  "#888",
        "deploy":    "#27ae60",
        "deploy_h":  "#2ecc71",
        "start":     "#2980b9",
        "start_h":   "#3498db",
        "danger":    "#e74c3c",
        "log_bg":    "#0a0a14",
    }

    def __init__(self):
        self.root = Tk()
        self.root.title("旅途星辰 TripStar — 部署与启动工具")
        self.root.geometry("780x680")
        self.root.minsize(680, 580)
        self.root.configure(bg=self.COLORS["bg"])

        # 设置图标
        try:
            icon_path = PROJECT_DIR / "frontend" / "favicon.png"
            if icon_path.exists():
                img = __import__("tkinter").PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, img)
        except Exception:
            pass

        self._busy = False
        self._worker = None
        self._msg_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

        # 居中
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- UI 构建 ----------
    def _build_ui(self):
        root = self.root

        # === 标题 ===
        title_frame = Frame(root, bg=self.COLORS["bg"])
        title_frame.pack(fill=X, padx=24, pady=(18, 4))
        Label(title_frame, text="旅途星辰 TripStar",
              font=("Microsoft YaHei", 20, "bold"),
              fg="#e8e8ff", bg=self.COLORS["bg"]).pack(anchor="w")
        Label(title_frame, text="一键部署与启动工具",
              font=("Microsoft YaHei", 10),
              fg=self.COLORS["text_dim"], bg=self.COLORS["bg"]).pack(anchor="w")

        # === 步骤面板（由后续操作动态创建） ===
        self.step_container = Frame(root, bg=self.COLORS["bg"])
        self.step_container.pack(fill=BOTH, expand=True, padx=24, pady=(12, 0))

        # 占位文字（初始状态）
        self.placeholder = Label(
            self.step_container,
            text="点击下方按钮开始操作\n\n"
                 "「🚀 部署项目」— 完整安装依赖与环境配置\n"
                 "「▶  启动项目」— 检查部署状态并启动服务",
            font=("Microsoft YaHei", 10),
            fg=self.COLORS["text_dim"], bg=self.COLORS["bg"],
            justify="center",
        )
        self.placeholder.pack(expand=True)

        self.step_panel = None  # 后面动态创建

        # === 按钮栏 ===
        btn_frame = Frame(root, bg=self.COLORS["bg"])
        btn_frame.pack(fill=X, padx=24, pady=(8, 4))

        self.deploy_btn = self._mkbtn(btn_frame, " 🚀  部署项目  ",
                                       self.COLORS["deploy"], self.COLORS["deploy_h"],
                                       self._on_deploy)
        self.deploy_btn.pack(side=LEFT, padx=(0, 10))

        self.start_btn = self._mkbtn(btn_frame, " ▶  启动项目  ",
                                      self.COLORS["start"], self.COLORS["start_h"],
                                      self._on_start)
        self.start_btn.pack(side=LEFT)

        # === 前后端实时运行状态灯 ===
        status_lamp_frame = Frame(btn_frame, bg=self.COLORS["bg"])
        status_lamp_frame.pack(side=RIGHT)

        Label(status_lamp_frame, text="  ", font=("Microsoft YaHei", 10),
              fg=self.COLORS["text_dim"], bg=self.COLORS["bg"]).pack(side=LEFT)

        # 后端状态灯
        self._be_label = Label(status_lamp_frame, text="●", font=("Consolas", 12),
                               fg="#444", bg=self.COLORS["bg"])
        self._be_label.pack(side=LEFT, padx=(0, 2))
        Label(status_lamp_frame, text="后端", font=("Microsoft YaHei", 9),
              fg=self.COLORS["text_dim"], bg=self.COLORS["bg"]).pack(side=LEFT, padx=(0, 10))

        # 前端状态灯
        self._fe_label = Label(status_lamp_frame, text="●", font=("Consolas", 12),
                               fg="#444", bg=self.COLORS["bg"])
        self._fe_label.pack(side=LEFT, padx=(0, 2))
        Label(status_lamp_frame, text="前端", font=("Microsoft YaHei", 9),
              fg=self.COLORS["text_dim"], bg=self.COLORS["bg"]).pack(side=LEFT, padx=(0, 2))

        # 监控控制
        self._services_started = False
        self._monitor_job = None
        self._fe_monitor_url = "http://127.0.0.1:5173"

        # 底部状态标签 + 清空按钮
        bottom_bar = Frame(root, bg=self.COLORS["bg"])
        bottom_bar.pack(fill=X, padx=24, pady=(2, 0))

        self.status_label = Label(
            bottom_bar, text="就绪", font=("Microsoft YaHei", 9),
            fg=self.COLORS["text_dim"], bg=self.COLORS["bg"], anchor="w",
        )
        self.status_label.pack(side=LEFT, fill=X, expand=True)

        self.clear_btn = self._mkbtn(bottom_bar, " 🗑  清空日志  ",
                                      "#555", "#666", self._on_clear, font_size=9, padx=10, pady=3)
        self.clear_btn.pack(side=RIGHT)

        # === 日志栏 ===
        log_frame = Frame(root, bg="#0a0a14", highlightbackground="#1a1a2e",
                          highlightthickness=1)
        log_frame.pack(fill=BOTH, padx=24, pady=(6, 16), ipady=0)

        # 日志标题
        log_header = Frame(log_frame, bg="#0f0f1a")
        log_header.pack(fill=X)
        Label(log_header, text="📋 运行日志",
              font=("Microsoft YaHei", 8, "bold"),
              fg=self.COLORS["text_dim"], bg="#0f0f1a").pack(anchor="w", padx=8, pady=4)

        self.log_text = Text(
            log_frame,
            bg=self.COLORS["log_bg"],
            fg="#a0a0b0",
            font=("Consolas", 9),
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=4,
            wrap="word",
            state=DISABLED,
            height=8,
        )
        self.log_text.pack(fill=BOTH, expand=True)

        # 滚动条
        scrollbar = __import__("tkinter").Scrollbar(self.log_text)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)

        # 日志 tag
        self.log_text.tag_config("ok",    foreground="#4caf50")
        self.log_text.tag_config("warn",  foreground="#ff9800")
        self.log_text.tag_config("error", foreground="#f44336")
        self.log_text.tag_config("info",  foreground="#a0a0b0")

    def _mkbtn(self, parent, text, color, hover_color, command,
               font_size=12, padx=18, pady=9):
        btn = Button(
            parent, text=text,
            font=("Microsoft YaHei", font_size, "bold"),
            fg="white", bg=color,
            activeforeground="white", activebackground=hover_color,
            relief="flat", borderwidth=0, cursor="hand2",
            padx=padx, pady=pady, command=command,
        )

        def on_enter(e, c=hover_color):
            if not self._busy:
                e.widget.config(bg=c)
        def on_leave(e, c=color):
            e.widget.config(bg=c)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn

    # ---------- 步骤面板管理 ----------
    def _switch_to_steps(self, steps, scroll_to_end=True):
        """切换到某组步骤面板"""
        if self.step_panel:
            self.step_panel.pack_forget()
            self.step_panel.destroy()
        self.placeholder.pack_forget()

        self.step_panel = StepPanel(self.step_container, steps)
        self.step_panel.pack(fill=BOTH, expand=True)
        return self.step_panel

    # ---------- 日志 ----------
    def _log(self, text, level="info"):
        self._msg_queue.put(("log", (text, level)))

    def _step_cb_from_thread(self, action, step_id, desc, detail=""):
        """从工作线程发来的步骤回调"""
        self._msg_queue.put(("step", (action, step_id, desc, detail)))

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self._msg_queue.get_nowait()
                if msg_type == "log":
                    text, level = data
                    self._do_log(text, level)
                elif msg_type == "step":
                    action, step_id, desc, detail = data
                    if self.step_panel:
                        self.step_panel.set_step(step_id, action, detail)
                        if action == "running":
                            self.set_status(f"▶ {desc}...", "running")
                        elif action == "success":
                            self.set_status(f"✓ {desc}", "success")
                        elif action == "fail":
                            self.set_status(f"✗ {desc}", "fail")
                elif msg_type == "status":
                    text, level = data
                    self.set_status(text, level)
                elif msg_type == "svc_status":
                    be_alive, fe_alive = data
                    self._update_service_indicators(be_alive, fe_alive)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_queue)

    def _do_log(self, text, level="info"):
        self.log_text.config(state=NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        if text:
            self.log_text.insert(END, f"[{ts}] {text}\n", level)
        self.log_text.see(END)
        self.log_text.config(state=DISABLED)

    def set_status(self, text, level="info"):
        color_map = {
            "success": self.COLORS["deploy"],
            "fail":    self.COLORS["danger"],
            "running": self.COLORS["start"],
            "info":    self.COLORS["text_dim"],
        }
        self.status_label.config(text=text, fg=color_map.get(level, self.COLORS["text_dim"]))

    # ---------- 按钮回调 ----------
    def _set_busy(self, busy):
        self._busy = busy
        state = DISABLED if busy else NORMAL
        self.deploy_btn.config(state=state)
        self.start_btn.config(state=state)
        self.root.update()

    def _run_worker(self, mode):
        """mode: 'deploy' or 'start'"""
        if self._busy:
            return

        self._set_busy(True)
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, END)
        self.log_text.config(state=DISABLED)

        steps = DEPLOY_STEPS if mode == "deploy" else START_STEPS
        self._switch_to_steps(steps)

        # 停止之前的状态灯监控
        self.root.after(0, self._stop_service_monitor)

        def _run():
            worker = DeployWorker(
                step_callback=self._step_cb_from_thread,
                log_callback=self._log,
                ask_callback=self._ask_from_worker,
            )
            if mode == "deploy":
                success = worker.deploy()
                self._msg_queue.put(("status", (
                    "✅ 部署完成！请配置 API Key 后点击「启动项目」" if success
                    else "❌ 部署失败，请查看日志",
                    "success" if success else "fail"
                )))
            else:
                result = worker.start()
                if result == "need_deploy":
                    self._msg_queue.put(("status", ("⚠ 部署不完整", "fail")))
                    self.root.after(0, self._ask_deploy)
                elif isinstance(result, tuple) and result[0] == "started":
                    port = result[1]
                    fe_url = result[2] if len(result) >= 3 else "http://localhost:5173"
                    self._fe_monitor_url = fe_url
                    self._msg_queue.put(("status", (
                        f"✅ 服务已启动！前端: {fe_url}", "success")))
                    self.root.after(0, self._start_service_monitor)
                    self.root.after(0, self._ask_browser, port, fe_url)
                else:
                    self._msg_queue.put(("status", (
                        "❌ 启动失败，请查看日志", "fail")))
            self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=_run, daemon=True).start()

    def _ask_from_worker(self, title, message):
        """在后台线程中被调用 — 在主线程弹窗询问用户，返回 True/False"""
        result_box = [False]
        event = threading.Event()

        def _show():
            result_box[0] = messagebox.askyesno(title, message, icon="warning")
            event.set()

        self.root.after(0, _show)
        event.wait()
        return result_box[0]

    def _ask_browser(self, port, fe_url="http://localhost:5173"):
        """询问是否启动浏览器"""
        answer = messagebox.askyesno(
            "启动浏览器",
            "服务已启动！是否打开浏览器访问？\n\n"
            f"前端地址: {fe_url}\n"
            f"后端文档: http://localhost:{port}/docs",
            icon="question",
        )
        if answer:
            webbrowser.open(fe_url)
            if self.step_panel:
                self.step_panel.set_step("open_browser", "success", fe_url)
            self.set_status(f"✅ 服务已启动，浏览器已打开", "success")
        else:
            if self.step_panel:
                self.step_panel.set_step("open_browser", "skip", "用户已取消")
            self.set_status("✅ 服务已启动，浏览器未打开 (可手动访问)", "info")

    def _ask_deploy(self):
        answer = messagebox.askyesno(
            "项目尚未部署",
            "检测到项目尚未完成部署，是否立即部署？\n\n"
            "选择「是」→ 自动安装所有依赖\n"
            "选择「否」→ 返回主界面",
            icon="question",
        )
        if answer:
            self._run_worker("deploy")
        else:
            self.set_status("已取消 — 部署后再来启动吧", "info")

    def _is_deployed(self):
        """快速检查部署状态（不输出日志）"""
        checks = [
            BACKEND_DIR / ".venv",
            BACKEND_DIR / "node_modules",
            BACKEND_DIR / ".env",
            FRONTEND_DIR / "node_modules",
            FRONTEND_DIR / ".env",
        ]
        return all(p.exists() for p in checks)

    def _on_deploy(self):
        if self._is_deployed():
            messagebox.showinfo(
                "已部署",
                "项目已完成部署，无需重复部署。\n\n"
                "可直接点击「启动项目」运行。",
            )
            return
        self._run_worker("deploy")

    def _on_start(self):
        # 先检查部署状态，未部署则询问
        if not self._is_deployed():
            answer = messagebox.askyesno(
                "未部署",
                "检测到项目尚未部署，是否立即部署？\n\n"
                "选择「是」→ 自动安装所有依赖\n"
                "选择「否」→ 取消操作",
                icon="question",
            )
            if answer:
                self._run_worker("deploy")
            else:
                self.set_status("已取消操作", "info")
            return
        self._run_worker("start")

    def _on_clear(self):
        self.log_text.config(state=NORMAL)
        self.log_text.delete(1.0, END)
        self.log_text.config(state=DISABLED)
        self.set_status("日志已清空", "info")

    # ────────── 前后端运行状态监控 ──────────
    def _start_service_monitor(self):
        """启动定时监控（每 6 秒检查一次前后端状态）"""
        self._services_started = True
        self._be_once_running = False   # 后端是否曾运行成功
        self._fe_once_running = False   # 前端是否曾运行成功
        self._poll_services()

    def _stop_service_monitor(self):
        """停止监控"""
        self._services_started = False
        if self._monitor_job:
            try:
                self.root.after_cancel(self._monitor_job)
            except Exception:
                pass
            self._monitor_job = None
        # 指示灯恢复灰色
        self._be_label.config(fg="#444")
        self._fe_label.config(fg="#444")

    def _poll_services(self):
        if not self._services_started:
            return

        def _check():
            be_alive = self._http_ok("http://127.0.0.1:8000/docs")
            fe_alive = self._http_ok(self._fe_monitor_url)
            self._msg_queue.put(("svc_status", (be_alive, fe_alive)))

        threading.Thread(target=_check, daemon=True).start()
        self._monitor_job = self.root.after(6000, self._poll_services)

    @staticmethod
    def _http_ok(url):
        """快速检查服务是否可访问 — TCP 端口检测（超时 2 秒）"""
        import socket
        import re
        m = re.match(r'https?://([^:/]+)(?::(\d+))?', url)
        if m:
            host = m.group(1)
            port = int(m.group(2)) if m.group(2) else (443 if url.startswith("https") else 80)
            try:
                s = socket.create_connection((host, port), timeout=2)
                s.close()
                return True
            except Exception:
                return False
        return False

    def _update_service_indicators(self, be_alive, fe_alive):
        """更新指示灯颜色"""
        # 后端
        if be_alive:
            self._be_once_running = True
            self._be_label.config(fg="#4caf50")  # 绿色
        elif self._be_once_running:
            self._be_label.config(fg="#f44336")  # 红色（曾运行但崩溃）
        # 前端
        if fe_alive:
            self._fe_once_running = True
            self._fe_label.config(fg="#4caf50")
        elif self._fe_once_running:
            self._fe_label.config(fg="#f44336")

    def run(self):
        self.root.mainloop()


# ============================================================
# 入口
# ============================================================
def main():
    os.chdir(str(PROJECT_DIR))
    try:
        gui = DeployGUI()
        gui.run()
    except ImportError:
        print("错误: tkinter 不可用。")
        print("  Windows: 重新安装 Python 时勾选 'tcl/tk and IDLE'")
        print("  Linux:   sudo apt install python3-tk")
        print("  macOS:   brew install python-tk")
        input("\n按 Enter 退出...")
        sys.exit(1)


if __name__ == "__main__":
    main()
