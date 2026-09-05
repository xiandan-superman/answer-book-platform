#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_ICON = ROOT / "assets" / "app-icon" / "app-icon-transparent.png"
APP_WINDOW_ICON = (
    ROOT / "assets" / "app-icon" / "app-icon.ico"
    if sys.platform.startswith("win")
    else ROOT / "assets" / "app-icon" / "app-icon.icns"
    if sys.platform == "darwin"
    else APP_ICON
)


def record_launcher_failure(stage: str) -> None:
    configured = os.environ.get("ANSWER_BOOK_LAUNCHER_BOOTSTRAP_LOG", "").strip()
    if configured:
        path = Path(configured)
    else:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        path = base / "Answer Book Platform" / "runtime" / "launcher-bootstrap.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"launcher_failure_stage={stage}\n")
            log.write(traceback.format_exc())
            log.write("\n")
    except OSError:
        pass


def run_with_shell_runtime(executable: Path) -> NoReturn:
    """Run the GUI with its managed runtime without Windows execv quoting loss."""
    command = [str(executable), str(Path(__file__).resolve()), *sys.argv[1:]]
    kwargs: dict[str, Any] = {"cwd": ROOT}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **kwargs)
    raise SystemExit(result.returncode)


try:
    from scripts.source_launcher import (  # noqa: E402
        RUNTIME_ENV_NAME,
        ensure_dependencies,
        local_service_url,
        python_executable_supported,
        runtime_python,
        user_data_root,
    )
except Exception:
    record_launcher_failure("import")
    raise

PORT = 8766
LAUNCHER_PORT = 18876


def service_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/version", timeout=1.2) as response:
            return response.status == 200
    except Exception:
        return False


def current_lan_mode(url: str) -> bool | None:
    try:
        with urllib.request.urlopen(f"{url}/api/lan/access", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("listening_on_lan"))
    except Exception:
        return None


def launcher_command(mode: str, port: int = PORT) -> list[str]:
    host = "0.0.0.0" if mode == "lan" else "127.0.0.1"
    return [sys.executable, str(ROOT / "scripts" / "source_launcher.py"), "--host", host, "--port", str(port)]


class LauncherController:
    def __init__(self, *, port: int) -> None:
        self.port = port
        self.url = local_service_url("127.0.0.1", port)
        self.mode = "local"
        self.status = "idle"
        self.message = "选择访问方式后启动平台"
        self.process: subprocess.Popen[Any] | None = None
        self.log_file: Any = None
        self.started_at = 0.0
        self.run_id = ""
        self.lock = threading.Lock()
        self.window: Any = None
        self.tray: Any = None
        self.exit_requested = False
        self.hidden_notice_sent = False

    @property
    def log_path(self) -> Path:
        return user_data_root() / "runtime" / "launcher.log"

    @property
    def progress_path(self) -> Path:
        return user_data_root() / "runtime" / "startup-progress.json"

    def _progress_snapshot(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if payload.get("run_id") != self.run_id:
            return {}
        allowed = {
            "status", "percent", "message", "current_component", "failed_component", "hint",
            "current_index", "completed_count", "total_count", "inactive_seconds", "progress_mode", "pending_components",
        }
        return {key: payload[key] for key in allowed if key in payload}

    def snapshot(self) -> dict[str, Any]:
        if service_ready(self.url):
            lan_mode = current_lan_mode(self.url)
            with self.lock:
                self.status = "ready"
                self.mode = "lan" if lan_mode else "local"
                self.message = "局域网监控已开启" if lan_mode else "平台已在本机运行"
        progress = self._progress_snapshot() if self.status in {"starting", "failed"} else {}
        with self.lock:
            status = "failed" if progress.get("status") == "failed" else self.status
            message = str(progress.get("message") or self.message)
            return {
                "ok": True,
                "status": status,
                "stage": progress.get("status", ""),
                "message": message,
                "mode": self.mode,
                "platform_url": self.url,
                "can_stop": bool(self.process is not None and self.process.poll() is None),
                "log_path": str(self.log_path),
                **{key: value for key, value in progress.items() if key not in {"status", "message"}},
            }

    def start(self, mode: str) -> tuple[bool, str]:
        if mode not in {"local", "lan"}:
            return False, "启动方式无效"
        if service_ready(self.url):
            lan_mode = current_lan_mode(self.url)
            if mode == "lan" and lan_mode is False:
                return False, "平台当前仅允许本机访问。请先停止当前平台，再开启局域网监控。"
            with self.lock:
                self.status, self.mode, self.message = "ready", "lan" if lan_mode else "local", "平台已在运行"
            webbrowser.open(self.url)
            return True, self.message
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return True, "平台正在启动"
            self.mode, self.status = mode, "starting"
            self.message = "正在检查运行环境，首次启动可能需要几分钟"
            self.run_id = str(time.time_ns())
        try:
            self.progress_path.unlink(missing_ok=True)
        except OSError:
            pass
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_path.open("w", encoding="utf-8", errors="replace")
        env = os.environ.copy()
        env["ANSWER_BOOK_GUI_LAUNCHER"] = "1"
        env["ANSWER_BOOK_STARTUP_PROGRESS_PATH"] = str(self.progress_path)
        env["ANSWER_BOOK_STARTUP_RUN_ID"] = self.run_id
        kwargs: dict[str, Any] = {"cwd": ROOT, "env": env, "stdout": self.log_file, "stderr": subprocess.STDOUT}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(launcher_command(mode, self.port), **kwargs)
        except Exception as exc:
            with self.lock:
                self.status, self.message = "failed", f"启动器无法运行：{exc}"
            return False, self.message
        self.started_at = time.monotonic()
        threading.Thread(target=self._monitor, daemon=True).start()
        return True, self.message

    def _monitor(self) -> None:
        while self.process is not None and self.process.poll() is None:
            if service_ready(self.url):
                with self.lock:
                    self.status = "ready"
                    self.message = "局域网监控已开启" if self.mode == "lan" else "平台已启动"
                time.sleep(0.8)
                self.hide_shell(automatic=True)
                return
            if time.monotonic() - self.started_at >= 8:
                with self.lock:
                    self.message = "正在准备运行依赖，请保持此页面打开"
            time.sleep(0.7)
        if not service_ready(self.url):
            progress = self._progress_snapshot()
            with self.lock:
                self.status = "failed"
                self.message = str(progress.get("message") or "启动没有完成，请查看日志后重试")
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def stop(self) -> tuple[bool, str]:
        if self.process is None or self.process.poll() is not None:
            return False, "当前启动页无法停止由其他窗口启动的平台"
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
            else:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            self.process.terminate()
        with self.lock:
            self.status, self.message = "idle", "平台已停止"
        return True, self.message

    def show_shell(self) -> None:
        if self.window is None:
            return
        try:
            self.window.restore()
            self.window.show()
        except Exception:
            pass

    def open_platform(self) -> None:
        webbrowser.open(self.url)

    def hide_shell(self, *, automatic: bool = False) -> None:
        if self.window is None:
            return
        try:
            if self.tray is None:
                self.window.minimize()
            else:
                self.window.hide()
                if automatic and not self.hidden_notice_sent and getattr(self.tray, "HAS_NOTIFICATION", False):
                    self.tray.notify("平台正在后台运行，可从托盘图标重新打开。", "真题解析与生题平台")
                    self.hidden_notice_sent = True
        except Exception:
            pass

    def request_window_close(self) -> bool:
        if self.exit_requested:
            return True
        self.hide_shell()
        return False

    def stop_and_exit(self) -> None:
        self.exit_requested = True
        self.stop()
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass


class LauncherHandler(BaseHTTPRequestHandler):
    server: "LauncherServer"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        return origin in {"", f"http://127.0.0.1:{self.server.server_port}"} and self.headers.get("X-Launcher-Request") == "1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/state":
            self._json(self.server.controller.snapshot())
            return
        if self.path == "/health":
            self._json({"ok": True})
            return
        if self.path == "/show":
            self.server.controller.show_shell()
            self._json({"ok": True})
            return
        if self.path == "/app-icon.png":
            if not APP_ICON.is_file():
                self.send_error(404)
                return
            body = APP_ICON.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path not in {"/", "/index.html"}:
            self.send_error(404)
            return
        body = (ROOT / "web" / "launcher.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if not self._same_origin():
            self._json({"ok": False, "message": "请求来源无效"}, 403)
            return
        try:
            length = min(1024, int(self.headers.get("Content-Length", "0")))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"ok": False, "message": "请求内容无效"}, 400)
            return
        if self.path == "/api/start":
            ok, message = self.server.controller.start(str(payload.get("mode") or "local"))
        elif self.path == "/api/stop":
            ok, message = self.server.controller.stop()
        elif self.path == "/api/open":
            webbrowser.open(self.server.controller.url)
            ok, message = True, "已打开"
        else:
            self._json({"ok": False, "message": "接口不存在"}, 404)
            return
        self._json({"ok": ok, "message": message}, 200 if ok else 409)


class LauncherServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], controller: LauncherController) -> None:
        super().__init__(address, LauncherHandler)
        self.controller = controller


def bootstrap_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.8) as response:
            return response.status == 200
    except Exception:
        return False


class PreparationWindow:
    def __init__(self) -> None:
        self.window: Any = None
        self.stage: Any = None
        self.message: Any = None
        self.detail: Any = None
        self.bar: Any = None
        try:
            import tkinter as tk
            from tkinter import ttk

            window = tk.Tk()
            window.title("真题解析与生题平台")
            window.geometry("500x280")
            window.resizable(False, False)
            window.protocol("WM_DELETE_WINDOW", lambda: None)
            if APP_ICON.is_file():
                self.icon = tk.PhotoImage(file=str(APP_ICON))
                window.iconphoto(True, self.icon)
            frame = tk.Frame(window, bg="#F6F8FC", padx=30, pady=27)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text="正在准备启动环境", bg="#F6F8FC", fg="#15213B",
                     font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
            self.stage = tk.Label(frame, text="检查运行环境", bg="#F6F8FC", fg="#246BFD",
                                  font=("Microsoft YaHei UI", 10, "bold"))
            self.stage.pack(anchor="w", pady=(14, 0))
            self.message = tk.Label(frame, text="首次启动需要安装必要组件，请保持网络连接。", bg="#F6F8FC", fg="#6F7B91",
                                    font=("Microsoft YaHei UI", 10), wraplength=430, justify="left")
            self.message.pack(anchor="w", pady=(6, 12))
            style = ttk.Style(window)
            style.configure("Bootstrap.Horizontal.TProgressbar", troughcolor="#E2E8F2", background="#246BFD")
            self.bar = ttk.Progressbar(frame, maximum=100, mode="determinate", style="Bootstrap.Horizontal.TProgressbar")
            self.bar.pack(fill="x")
            self.detail = tk.Label(frame, text="正在扫描所需组件…", bg="#F6F8FC", fg="#8792A6",
                                   font=("Microsoft YaHei UI", 9), wraplength=430, justify="left")
            self.detail.pack(anchor="w", pady=(10, 0))
            window.update_idletasks()
            window.update()
            self.window = window
        except Exception:
            self.window = None
            if sys.platform == "darwin" and shutil.which("osascript"):
                subprocess.run(
                    ["osascript", "-e", 'display notification "首次启动正在准备必要组件，请稍候。" with title "真题解析与生题平台"'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    def update(self, status: str, percent: int, message: str, **details: Any) -> None:
        if self.window is None:
            return
        try:
            stage_names = {
                "checking_dependencies": "检查运行环境",
                "creating_environment": "创建专用环境",
                "dependencies_found": "发现待安装组件",
                "resolving_dependencies": "解析组件版本",
                "downloading_dependencies": "下载并准备组件",
                "installing_dependencies": "安装运行组件",
                "verifying_dependencies": "验证安装结果",
                "dependencies_ready": "运行组件已就绪",
                "failed": "准备失败",
            }
            self.stage.configure(text=stage_names.get(status, "准备启动环境"), fg="#D14343" if status == "failed" else "#246BFD")
            self.message.configure(text=message)
            total = int(details.get("total_count") or 0)
            completed = int(details.get("completed_count") or 0)
            component = str(details.get("current_component") or details.get("failed_component") or "")
            detail_parts = []
            if component:
                detail_parts.append(f"当前组件：{component}")
            if total:
                current_index = int(details.get("current_index") or 0)
                detail_parts.append(f"进度：第 {current_index}/{total} 项" if current_index else f"已完成：{completed}/{total} 项")
            pending = details.get("pending_components")
            if isinstance(pending, list) and pending and not component:
                preview = "、".join(str(item) for item in pending[:6])
                suffix = f" 等 {len(pending)} 项" if len(pending) > 6 else ""
                detail_parts.append(f"待安装：{preview}{suffix}")
            inactive = int(details.get("inactive_seconds") or 0)
            if inactive >= 45:
                detail_parts.append(f"最近活动：{inactive} 秒前")
            if details.get("hint"):
                detail_parts.append(str(details["hint"]))
            self.detail.configure(text="  ·  ".join(detail_parts) or "正在扫描所需组件…")
            if details.get("progress_mode") == "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(12)
            else:
                self.bar.stop()
                self.bar.configure(mode="determinate", value=max(0, min(100, int(percent))))
            self.window.update_idletasks()
            self.window.update()
        except Exception:
            self.window = None

    def close(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


def ensure_shell_runtime() -> None:
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        return
    data_root = user_data_root().resolve()
    target = runtime_python(data_root / "runtime" / RUNTIME_ENV_NAME)
    shell_target = target.with_name("pythonw.exe") if sys.platform.startswith("win") else target
    if not shell_target.is_file():
        shell_target = target
    try:
        same_runtime = shell_target.is_file() and Path(sys.executable).resolve() == shell_target.resolve()
    except OSError:
        same_runtime = False
    if same_runtime:
        try:
            __import__("webview")
            __import__("pystray")
            return
        except ImportError:
            pass
    if target.is_file() and python_executable_supported(target):
        probe = subprocess.run([str(target), "-c", "import webview,pystray"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            run_with_shell_runtime(shell_target)
    os.environ["ANSWER_BOOK_GUI_LAUNCHER"] = "1"
    progress = PreparationWindow()
    try:
        python = ensure_dependencies(ROOT, data_root, approved=False, progress=progress.update)
    except Exception as exc:
        progress.close()
        hint = getattr(exc, "hint", "请检查网络后重新尝试；若仍失败，请保留启动日志并联系维护人员。")
        error_message = f"启动环境准备失败。\n\n{exc}\n\n{hint}\n\n日志：{user_data_root() / 'runtime' / 'launcher.log'}"
        try:
            from tkinter import messagebox

            messagebox.showerror("真题解析与生题平台", error_message)
        except Exception:
            if sys.platform == "darwin" and shutil.which("osascript"):
                safe_message = error_message.replace('"', "'")[:600]
                subprocess.run(
                    ["osascript", "-e", f'display dialog "启动环境准备失败。\\n\\n{safe_message}" buttons {{"关闭"}} with title "真题解析与生题平台"'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        raise
    progress.close()
    shell_python = python.with_name("pythonw.exe") if sys.platform.startswith("win") else python
    if not shell_python.is_file():
        shell_python = python
    run_with_shell_runtime(shell_python)


def run_desktop_shell(server: LauncherServer, controller: LauncherController, launcher_url: str) -> bool:
    try:
        import webview
    except ImportError:
        return False
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    window = webview.create_window(
        "启动真题解析与生题平台",
        launcher_url,
        width=760,
        height=700,
        min_size=(680, 620),
        resizable=True,
        confirm_close=False,
        background_color="#F6F8FC",
        text_select=False,
    )
    controller.window = window
    window.events.closing += controller.request_window_close
    try:
        import pystray
        from PIL import Image

        image = Image.open(APP_ICON).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        tray = pystray.Icon(
            "answer_book_platform",
            image,
            "真题解析与生题平台",
            menu=pystray.Menu(
                pystray.MenuItem("打开平台", lambda _icon, _item: controller.open_platform(), default=True),
                pystray.MenuItem("显示启动器", lambda _icon, _item: controller.show_shell()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("停止并退出", lambda _icon, _item: controller.stop_and_exit()),
            ),
        )
        controller.tray = tray
        if sys.platform == "darwin":
            from AppKit import NSApplication

            tray.run_detached(darwin_nsapplication=NSApplication.sharedApplication())
        else:
            tray.run_detached()
    except Exception:
        controller.tray = None
    try:
        webview.start(private_mode=True, icon=str(APP_WINDOW_ICON))
    finally:
        if not controller.exit_requested:
            controller.stop()
        server.shutdown()
        server.server_close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("local", "lan"))
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--port", default=PORT, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--launcher-port", default=LAUNCHER_PORT, type=int, help=argparse.SUPPRESS)
    parser.add_argument("--skip-runtime-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.skip_runtime_check:
        ensure_shell_runtime()
    launcher_url = f"http://127.0.0.1:{args.launcher_port}/"
    if bootstrap_ready(args.launcher_port):
        urllib.request.urlopen(f"http://127.0.0.1:{args.launcher_port}/show", timeout=1).close()
        return 0
    controller = LauncherController(port=args.port)
    server = LauncherServer(("127.0.0.1", args.launcher_port), controller)
    if args.mode and args.autostart:
        controller.start(args.mode)
    if run_desktop_shell(server, controller, launcher_url):
        return 0
    webbrowser.open(launcher_url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        record_launcher_failure("main")
        raise
