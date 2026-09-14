@echo off
REM ===============================================================
REM  MX Anywhere 3    滚轮后键  转  微信输入法 语音输入
REM  按住说话，松手转文字
REM
REM  不需要管理员权限：HID++ 的 divert 在普通用户下就能工作，已实测。
REM  想看实时日志就用这个；不想看到黑框就用桌面上的「语音键-控制面板」。
REM ===============================================================
setlocal
cd /d "%~dp0"

set PY=C:\Users\75202\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=C:\Users\75202\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python
set PYW=C:\Users\75202\AppData\Local\Programs\Python\Python312\pythonw.exe
if not exist "%PYW%" set PYW=%PY%

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
