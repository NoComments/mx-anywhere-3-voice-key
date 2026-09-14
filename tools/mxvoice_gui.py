"""
mxvoice_gui.py -- desktop window for the MX Anywhere 3 wheel-rear voice key.

Wraps the same engine as mxvoice.py (HID++ divert + Ctrl+Alt injection) in a
small Tkinter window, so the remap can be started and stopped without a
console window hanging around.

No third-party packages: Tkinter ships with CPython. If the interpreter that
launches this file happens to be a build without Tkinter (some embedded /
managed distributions are), the bootstrap below re-executes the script with a
Python that does have it, so `python mxvoice_gui.py` works either way.

Run:  python mxvoice_gui.py
      (or double-click  语音键-控制面板.bat  on the Desktop)
"""
from __future__ import annotations

import os
import sys

# --------------------------------------------------------------- bootstrap --
# Must run before `import tkinter`, so it sits above every other import.

_BOOT_FLAG = "MXV_GUI_REEXEC"


def _candidate_pythons():
    """Plausible interpreters that might have Tkinter, best guess first."""
    import glob

    seen = set()
    out = []

    def add(p):
        if not p:
            return
        p = os.path.abspath(p)
        k = p.lower()
        if k in seen or not os.path.isfile(p):
            return
        seen.add(k)
        out.append(p)

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        for pat in (
            os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
            r"C:\Python3*\python.exe",
            os.path.join(pf, "Python3*", "python.exe"),
            os.path.join(pfx86, "Python3*", "python.exe"),
        ):
            for p in sorted(glob.glob(pat), reverse=True):
                add(p)

    import shutil
    for name in ("python", "python3"):
        add(shutil.which(name))

    return out


def _has_tkinter(exe: str) -> bool:
    import subprocess
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([exe, "-c", "import tkinter"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, creationflags=flags)
        return r.returncode == 0
    except Exception:
        return False


def _ensure_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
        return
    except Exception:
        pass

    if os.environ.get(_BOOT_FLAG):
        sys.stderr.write(
            "[!] 当前 Python 没有 tkinter，也没找到其它带 tkinter 的 Python。\n"
            "    请安装完整版 Python，或在命令行里用完整版 Python 运行本文件。\n")
        raise SystemExit(2)

    me = os.path.abspath(sys.executable).lower()
    for exe in _candidate_pythons():
        if exe.lower() == me:
            continue
        if _has_tkinter(exe):
            env = dict(os.environ)
            env[_BOOT_FLAG] = "1"
            # NOTE: os.execv*/execve on Windows joins argv with plain spaces --
            # it does NOT quote arguments containing spaces. This project lives
            # under "C:\Users\...\WorkBuddy AI\...", so without the explicit
            # quotes the child gets "...\WorkBuddy" as the script name and dies
            # with "can't find '__main__' module". Quote it ourselves.
            script = '"%s"' % os.path.abspath(__file__)
            os.execve(exe, [exe, script, *sys.argv[1:]], env)

    sys.stderr.write(
        "[!] 没找到带 tkinter 的 Python。\n"
        "    提示：普通安装版 Python（python.org 下载）都自带 tkinter。\n")
    raise SystemExit(2)


_ensure_tkinter()

# ------------------------------------------------------------------ imports --

import contextlib          # noqa: E402
import queue               # noqa: E402
import threading           # noqa: E402
import tkinter as tk       # noqa: E402
from tkinter import scrolledtext   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hidpp            # noqa: E402
import mxvoice as mv    # noqa: E402

PYTHON = sys.executable
HEADLESS = os.path.join(HERE, "mxvoice.py")
SOLUTION = os.path.join(HERE, "SOLUTION.md")


def _startup_dir() -> str:
    """The per-user Startup folder.

    APPDATA is the normal source, but it is absent when the process is spawned
    from a stripped-down shell (some sandboxes drop it), so fall back to the
    profile path.
    """
    roaming = os.environ.get("APPDATA")
    if not roaming:
        roaming = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(roaming, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")


STARTUP_DIR = _startup_dir()
# Same filename make_launchers.py uses, so the two never fight over the
# Startup folder and you can never end up with two auto-started copies.
STARTUP_BAT = os.path.join(STARTUP_DIR, "MX语音键.bat")

BG = "#1e1f22"
FG = "#e6e6e6"
DIM = "#9aa0a6"
PANEL = "#2b2d31"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"
BLUE = "#3a7afe"

# UTF-8 with BOM so cmd.exe and PowerShell both read the Chinese correctly.
HEADLESS_BAT = f"""@echo off
REM 开机自动启动：MX Anywhere 3 滚轮后键语音输入
REM 最小化运行，避免占屏幕；要停就在任务栏关掉那个窗口。
start "MX语音键" /min "{PYTHON}" "{HEADLESS}"
"""


# ---------------------------------------------------------------- engine ---

class QueueWriter:
    """Send print() output to the GUI instead of a console."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


class Engine(threading.Thread):
    """Runs mv.run() off the UI thread."""

    def __init__(self, q, do_inject=True, debounce_ms=mv.RELEASE_DEBOUNCE_MS):
        super().__init__(daemon=True)
        self.q = q
        self.do_inject = do_inject
        self.debounce_ms = debounce_ms
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        q = self.q
        handles = []
        try:
            q.put(("status", "正在查找设备"))
            h, path, caps, hr, hq = mv.find_mouse_handle()
            handles = [x for x in {h, hr, hq} if x]
            q.put(("log", f"[*] device : {path}\n"))

            dev = mv.HidppDevice(h, 0, hr, None, hq)
            feat = dev.get_feature_index(mv.FEAT_REPROG_CONTROLS_V4)
            if feat is None:
                q.put(("log", "[!] 这个设备不支持 0x1b04，无法接管按键。\n"))
                q.put(("status", "设备不支持"))
                return
            q.put(("log", f"[*] 0x1b04 REPROG_CONTROLS_V4 at feature "
                          f"index {feat}\n"))

            if not mv.acquire_singleton():
                q.put(("log",
                       "[!] 已经有另一个 mxvoice 在跑了。\n"
                       "    多半是开机自启的那个后台实例。\n"
                       "    要么先把「开机自动启动」取消并重启电脑，\n"
                       "    要么在任务管理器里结束掉 python.exe 再回来点启动。\n"))
                q.put(("status", "已有实例在运行"))
                return

            q.put(("status", "运行中"))
            with contextlib.redirect_stdout(QueueWriter(q)):
                mv.run(h, feat, do_inject=self.do_inject,
                       read_handle=hr, req_handle=hq,
                       debounce_ms=self.debounce_ms,
                       stop_event=self.stop_event)
            q.put(("status", "已停止"))

        except SystemExit as e:
            q.put(("log", f"[!] 找不到设备: {e}\n"))
            q.put(("status", "找不到设备"))
        except Exception as e:                      # noqa: BLE001
            q.put(("log", f"[!] 出错: {e}\n"))
            q.put(("status", "出错"))
        finally:
            for x in handles:
                try:
                    hidpp.close(x)
                except Exception:
                    pass
            q.put(("done", None))


# ------------------------------------------------------------ autostart ---

def autostart_enabled() -> bool:
    return os.path.isfile(STARTUP_BAT)


def set_autostart(on: bool) -> None:
    """Write/remove the Startup-folder launcher that runs the headless engine.

    Content is pure ASCII and written as GBK, because cmd.exe reads .bat files
    in the system ANSI codepage -- a UTF-8 file with Chinese in it gets mangled
    and the window "flashes and vanishes".
    """
    if on:
        os.makedirs(STARTUP_DIR, exist_ok=True)
        with open(STARTUP_BAT, "w", encoding="gbk", newline="\r\n") as f:
            f.write(HEADLESS_BAT)
    elif os.path.isfile(STARTUP_BAT):
        os.remove(STARTUP_BAT)


# ------------------------------------------------------------------- gui ---

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        self.engine: "Engine | None" = None

        root.title("MX Anywhere 3  语音键")
        root.geometry("640x520")
        root.minsize(540, 420)
        root.configure(bg=BG)

        self._build()
        self._apply_autostart_state()
        self.root.after(100, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._append(
            "用法：按住滚轮后面那个键说话，松手自动转文字。\n"
            "前提：微信输入法 → 设置 → 语音输入 → 快捷键，设为 Ctrl+Alt。\n"
            "\n")

    # -- layout ------------------------------------------------------------
    def _build(self):
        pad = {"padx": 14}

        head = tk.Label(self.root, text="滚轮后键  →  微信输入法语音输入",
                        bg=BG, fg=FG, font=("Microsoft YaHei UI", 13, "bold"))
        head.pack(anchor="w", pady=(14, 2), **pad)

        sub = tk.Label(self.root,
                       text="按住滚轮后面那个键说话，松手自动转文字",
                       bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9))
        sub.pack(anchor="w", **pad)

        # status row
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", pady=(12, 6), **pad)
        self.dot = tk.Label(row, text="\u25cf", bg=BG, fg=DIM,
                            font=("Microsoft YaHei UI", 12))
        self.dot.pack(side="left")
        self.status = tk.Label(row, text="已停止", bg=BG, fg=DIM,
                               font=("Microsoft YaHei UI", 10))
        self.status.pack(side="left", padx=(6, 0))

        # buttons
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", pady=(2, 8), **pad)

        self.toggle = tk.Button(
            btns, text="启动", width=10, relief="flat", cursor="hand2",
            bg=BLUE, fg="white", activebackground="#2f66d6",
            activeforeground="white",
            font=("Microsoft YaHei UI", 10, "bold"),
            command=self._toggle)
        self.toggle.pack(side="left")

        self.clear = tk.Button(
            btns, text="清空日志", width=10, relief="flat", cursor="hand2",
            bg=PANEL, fg=FG, activebackground="#3a3d43",
            activeforeground=FG, font=("Microsoft YaHei UI", 9),
            command=self._clear_log)
        self.clear.pack(side="left", padx=(8, 0))

        self.help = tk.Button(
            btns, text="使用说明", width=10, relief="flat", cursor="hand2",
            bg=PANEL, fg=FG, activebackground="#3a3d43",
            activeforeground=FG, font=("Microsoft YaHei UI", 9),
            command=self._open_solution)
        self.help.pack(side="left", padx=(8, 0))

        # options
        opts = tk.Frame(self.root, bg=BG)
        opts.pack(fill="x", pady=(0, 8), **pad)

        self.dry_var = tk.BooleanVar(value=False)
        self.dry = tk.Checkbutton(
            opts, text="只记录不注入（排错用）", variable=self.dry_var,
            bg=BG, fg=DIM, selectcolor=PANEL, activebackground=BG,
            activeforeground=FG, font=("Microsoft YaHei UI", 9),
            highlightthickness=0)
        self.dry.pack(side="left")

        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        self.auto = tk.Checkbutton(
            opts, text="开机自动启动", variable=self.auto_var,
            bg=BG, fg=DIM, selectcolor=PANEL, activebackground=BG,
            activeforeground=FG, font=("Microsoft YaHei UI", 9),
            highlightthickness=0, command=self._toggle_autostart)
        self.auto.pack(side="left", padx=(16, 0))

        # log
        self.log = scrolledtext.ScrolledText(
            self.root, height=14, relief="flat", bg=PANEL, fg=FG,
            insertbackground=FG, font=("Consolas", 9), wrap="word",
            state="disabled")
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        foot = tk.Label(
            self.root,
            text="关掉窗口时，按键会自动恢复成原来的滚轮模式切换",
            bg=BG, fg=DIM, font=("Microsoft YaHei UI", 8))
        foot.pack(anchor="w", pady=(0, 12), **pad)

    # -- helpers -----------------------------------------------------------
    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str, color: str):
        self.status.configure(text=text, fg=color)
        self.dot.configure(fg=color)

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _apply_autostart_state(self):
        """Keep the checkbox honest if the Startup file changed on disk."""
        real = autostart_enabled()
        if self.auto_var.get() != real:
            self.auto_var.set(real)
        if real:
            self._append("[i] 开机自启已开启：登录后会有一个最小化的后台实例。\n"
                         "    那个实例在跑的时候，这里的「启动」会被拒绝。\n\n")

    def _toggle_autostart(self):
        try:
            set_autostart(self.auto_var.get())
            self._append(f"[i] 开机自动启动已"
                         f"{'开启' if self.auto_var.get() else '关闭'}\n")
        except Exception as e:                      # noqa: BLE001
            self.auto_var.set(autostart_enabled())
            self._append(f"[!] 改开机自启失败: {e}\n")

    def _open_solution(self):
        try:
            if os.path.isfile(SOLUTION):
                os.startfile(SOLUTION)          # noqa: S606 (Windows-only)
            else:
                self._append("[!] 没找到 SOLUTION.md\n")
        except Exception as e:                      # noqa: BLE001
            self._append(f"[!] 打不开说明: {e}\n")

    # -- run control -------------------------------------------------------
    def _toggle(self):
        if self.engine and self.engine.is_alive():
            self.toggle.configure(state="disabled", text="停止中…")
            self.engine.stop()
        else:
            self.engine = Engine(self.q, do_inject=not self.dry_var.get())
            self.engine.start()
            self.toggle.configure(text="停止")
            self.dry.configure(state="disabled")

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "status":
                    color = {"运行中": GREEN}.get(payload, DIM)
                    if payload in ("出错", "找不到设备", "设备不支持",
                                   "已有实例在运行"):
                        color = RED
                    if payload == "正在查找设备":
                        color = AMBER
                    self._set_status(payload, color)
                elif kind == "done":
                    self.engine = None
                    self.toggle.configure(state="normal", text="启动")
                    self.dry.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _on_close(self):
        if self.engine and self.engine.is_alive():
            self.engine.stop()
            self.engine.join(timeout=3)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        # Crisper text on high-DPI displays.
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = App(root)

    if "--selftest" in sys.argv:
        # Build the window, prove it renders, then quit. Used to verify the
        # install without needing a human to click anything.
        def _report():
            print("SELFTEST OK")
            print("  interpreter :", sys.executable)
            print("  tk version  :", root.call("info", "patchlevel"))
            print("  geometry    :",
                  f"{root.winfo_width()}x{root.winfo_height()}")
            print("  autostart   :", autostart_enabled())
            root.destroy()

        root.after(1200, _report)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
