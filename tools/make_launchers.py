"""
make_launchers.py -- generate the launcher .bat files with GBK encoding.

cmd.exe reads .bat files using the system ANSI codepage (GBK on this machine).
A UTF-8 file containing Chinese gets mangled, which is exactly why an earlier
launcher "flashed and vanished". So every .bat here is written as GBK.

Two ways to run the remap:
  * 语音键-控制面板.bat  -> mxvoice_gui.py under pythonw.exe (no console at all)
  * start-voice.bat      -> mxvoice.py in a console, for watching the log
Both are safe to run together: a named mutex makes the second one refuse to
start, so the button can never be diverted twice.

Every interpreter reference has a fallback chain, because the machine has more
than one Python and not all of them are guaranteed to stay installed.
"""
import io
import os

TOOLS = os.path.dirname(os.path.abspath(__file__))
DESKTOP = r"C:\Users\75202\Desktop"
STARTUP = (r"C:\Users\75202\AppData\Roaming\Microsoft\Windows\Start Menu"
           r"\Programs\Startup")

# Preferred interpreter: the full python.org install, which ships Tkinter (the
# GUI needs it) and tcl/. Keep the managed WorkBuddy one as a fallback, and a
# bare "python" as a last resort.
PY_FULL = r"C:\Users\75202\AppData\Local\Programs\Python\Python312\python.exe"
PYW_FULL = r"C:\Users\75202\AppData\Local\Programs\Python\Python312\pythonw.exe"
PY_ALT = (r"C:\Users\75202\.workbuddy-ai\binaries\python\versions"
          r"\3.13.12\python.exe")

# Resolve PY then PYW in the same order, so the console launcher and the GUI
# launcher never end up on different interpreters.
PY_PICK = f"""set PY={PY_FULL}
if not exist "%PY%" set PY={PY_ALT}
if not exist "%PY%" set PY=python
set PYW={PYW_FULL}
if not exist "%PYW%" set PYW=%PY%"""

START_VOICE = f"""@echo off
REM ===============================================================
REM  MX Anywhere 3    滚轮后键  转  微信输入法 语音输入
REM  按住说话，松手转文字
REM
REM  不需要管理员权限：HID++ 的 divert 在普通用户下就能工作，已实测。
REM  想看实时日志就用这个；不想看到黑框就用桌面上的「语音键-控制面板」。
REM ===============================================================
setlocal
cd /d "%~dp0"

{PY_PICK}

echo.
echo ===============================================================
echo   MX Anywhere 3    滚轮后键  =  微信输入法 语音输入
echo ===============================================================
echo.
echo   用之前先确认：
echo     微信输入法 - 设置 - 语音输入 - 快捷键   设为  Ctrl+Alt
echo.
echo   然后：按住滚轮后面那个键说话，松手自动转文字。
echo   Ctrl+C 退出，按键会自动恢复原状。
echo.
"%PY%" mxvoice.py
echo.
pause
"""

DESKTOP_GUI = f"""@echo off
REM 双击启动桌面控制面板：MX Anywhere 3 滚轮后键语音输入
REM 用 pythonw.exe，所以完全不会出现黑框窗口。
setlocal
cd /d "{TOOLS}"
{PY_PICK}
start "" "%PYW%" "{TOOLS}\\mxvoice_gui.py"
"""

DESKTOP_BAT = f"""@echo off
REM 双击启动（控制台版）：MX Anywhere 3 滚轮后键语音输入
call "{TOOLS}\\start-voice.bat"
"""

STARTUP_BAT = f"""@echo off
REM 开机自动启动：MX Anywhere 3 滚轮后键语音输入
REM 最小化运行，避免占屏幕；要停就在任务栏关掉那个窗口。
{PY_PICK}
start "MX语音键" /min "%PY%" "{TOOLS}\\mxvoice.py"
"""

TARGETS = [
    (os.path.join(TOOLS, "start-voice.bat"), START_VOICE),
    (os.path.join(DESKTOP, "语音键-控制面板.bat"), DESKTOP_GUI),
    (os.path.join(DESKTOP, "语音键-按住说话.bat"), DESKTOP_BAT),
    (os.path.join(STARTUP, "MX语音键.bat"), STARTUP_BAT),
]

if __name__ == "__main__":
    for path, text in TARGETS:
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            print(f"SKIP (no dir): {path}")
            continue
        try:
            with io.open(path, "w", encoding="gbk", newline="\r\n") as f:
                f.write(text)
        except PermissionError as e:
            print(f"FAIL (permission): {path}")
            print(f"      {e}")
            print("      The Startup / Desktop folders live outside the "
                  "workspace, so this needs the sandbox disabled.")
            continue
        print(f"written: {path}")

    print()
    print("--- 回读验证（重点看路径是反斜杠还是正斜杠）---")
    for path, _ in TARGETS:
        if not os.path.isfile(path):
            continue
        raw = io.open(path, "rb").read()
        for ln in raw.split(b"\r\n"):
            s = ln.strip()
            if s.startswith(b"set PY=") or s.startswith(b"set PYW=") \
                    or s.startswith(b"call ") or s.startswith(b"start "):
                # cmd switches like /min are not paths -- drop them first, or
                # the forward-slash count gives a false alarm.
                body = ln.replace(b"/min", b"").replace(b"/MAX", b"")
                fwd = body.count(b"/")
                bck = body.count(b"\\")
                flag = "OK " if bck and not fwd else "!! "
                print(f"  [{flag}]{os.path.basename(path)}")
                print(f"        {ln.decode('gbk', 'replace')}")
                print(f"        反斜杠={bck} 正斜杠={fwd}")
