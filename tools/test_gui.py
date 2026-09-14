"""
_gui_engine_test.py -- exercise the GUI's Engine code path without a window.

The GUI window was verified to open, but nothing had proven that pressing
"启动" actually finds the mouse, diverts 0xC4 and starts the loop. This drives
the exact same Engine class the button drives, runs it briefly, stops it, and
reports what the status queue saw.

Also exercises set_autostart() (write + remove), which is what the
"开机自动启动" checkbox calls.

Nothing is injected (do_inject=False), so it cannot type anything.

NOTE: if another mxvoice is already running (e.g. you started it from the
desktop, or the auto-start instance from login), the named mutex makes this
test refuse to start -- that is correct behaviour, not a failure. In that case
the engine section reports SKIP and tells you what to close first.
"""
import os
import queue
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mxvoice_gui as gui      # noqa: E402
import mxvoice as mv           # noqa: E402
import hidpp                   # noqa: E402

FAIL = []
SKIP = []


def check(label, ok, extra=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + extra) if extra else ''}")
    if not ok:
        FAIL.append(label)


def skip(label, why):
    print(f"  [SKIP] {label} -- {why}")
    SKIP.append(label)


def strip_switches(line: str) -> str:
    """cmd switches like /min are not paths -- drop them before counting slashes."""
    return line.replace("/min", "").replace("/MAX", "")


print("=" * 62)
print("0) 当前设备状态（只读，不改变任何东西）")
print("=" * 62)
try:
    h, path, caps, hr, hq = mv.find_mouse_handle()
    try:
        dev = mv.HidppDevice(h, 0, hr, None, hq)
        feat = dev.get_feature_index(mv.FEAT_REPROG_CONTROLS_V4)
        print(f"  设备: ...{path[-58:]}")
        print(f"  0x1b04 feature index = {feat}")

        # A second process asking for the reporting state can time out while
        # another instance is busy on the same device, so retry a few times
        # instead of treating a timeout as "not diverted".
        st = None
        last_err = None
        for attempt in range(3):
            try:
                st = dev.get_cid_reporting(0x00C4)
                break
            except Exception as e:            # noqa: BLE001
                last_err = e
                time.sleep(0.6)

        if st is None:
            print(f"  读 0xC4 上报状态失败（重试 3 次）: {last_err}")
            print("  -> 可能原因：另一个实例正在占用设备，或鼠标刚休眠。")
            print("     这一项只是参考信息，不影响后面的结论。")
        else:
            print(f"  0xC4 当前上报状态: diverted={st['diverted']} "
                  f"analytics={st.get('analytics')}")
            if st["diverted"]:
                print("  -> 已经被接管了，说明有一个 mxvoice 实例正在运行。")
            else:
                print("  -> 未被接管（当前没有实例在跑，或刚退出）。")
    finally:
        for x in {h, hr, hq}:
            if x:
                try:
                    hidpp.close(x)
                except Exception:
                    pass
except SystemExit as e:
    print(f"  找不到设备: {e}")

print()
print("=" * 62)
print("1) 开机自启开关（set_autostart 写/删）")
print("=" * 62)
print(f"  Startup 文件: {gui.STARTUP_BAT}")

before = gui.autostart_enabled()
print(f"  测试前 autostart_enabled() = {before}")

gui.set_autostart(False)
check("关闭后文件不存在", not os.path.isfile(gui.STARTUP_BAT),
      f"enabled={gui.autostart_enabled()}")

gui.set_autostart(True)
exists = os.path.isfile(gui.STARTUP_BAT)
check("开启后文件已写入", exists)
if exists:
    raw = open(gui.STARTUP_BAT, "rb").read()
    txt = raw.decode("gbk", "replace")
    start_line = [l for l in txt.splitlines() if l.strip().startswith("start ")]
    check("内容含 python 路径", "python" in txt.lower())
    if start_line:
        body = strip_switches(start_line[0])
        check("路径用的是反斜杠", "\\" in body and "/" not in body,
              start_line[0][:72])
    else:
        check("有 start 行", False)
    check("GBK 可解码（cmd 不会乱码）", "\ufffd" not in txt,
          repr(txt.strip().splitlines()[-1][:60]))
    check("autostart_enabled() 与文件一致", gui.autostart_enabled())

gui.set_autostart(before)          # 恢复成测试前的状态
check("已恢复原状态", gui.autostart_enabled() == before,
      f"enabled={gui.autostart_enabled()}")

print()
print("=" * 62)
print("2) GUI Engine（「启动」按钮实际走的代码路径）")
print("=" * 62)

q = queue.Queue()
eng = gui.Engine(q, do_inject=False)     # 不注入，安全
eng.start()

statuses = []
logs = []
deadline = time.time() + 10
done = False
while time.time() < deadline and not done:
    try:
        kind, payload = q.get(timeout=0.5)
    except queue.Empty:
        continue
    if kind == "status":
        statuses.append(payload)
    elif kind == "log":
        logs.append(payload)
    elif kind == "done":
        done = True

busy = "已有实例在运行" in statuses

# Stop the engine BEFORE printing anything. Engine.run() wraps mv.run() in
# contextlib.redirect_stdout, which is PROCESS-GLOBAL, not thread-local -- so
# while the engine thread is inside that block, every print() in this process
# (including from this thread) is swallowed into the GUI log queue. Printing
# first would make this test look like it produced no output.
stopped_elapsed = None
if not busy:
    t0 = time.time()
    eng.stop()
    eng.join(timeout=8)
    stopped_elapsed = time.time() - t0
    while True:
        try:
            kind, payload = q.get_nowait()
            if kind == "status":
                statuses.append(payload)
            elif kind == "log":
                logs.append(payload)
        except queue.Empty:
            break

# Safe to print now.
print(f"  状态序列: {statuses}")
print("  日志:")
for ln in "".join(logs).splitlines():
    print(f"    {ln}")

check("设备被找到并拿到 feature index", any("0x1b04" in l for l in logs))

if busy:
    skip("进入「运行中」", "已有另一个实例在跑，互斥体正确地拒绝了第二个实例")
    skip("退出后报告「已停止」", "同上")
    check("拒绝时给出了可操作的提示",
          any("任务管理器" in l or "开机自启" in l for l in logs))
    print()
    print("  -> 单实例保护验证通过。想跑完整流程，先关掉那个实例：")
    print("     任务管理器里结束 python.exe，或关掉它那个窗口，然后重跑本测试。")
else:
    check("进入「运行中」", "运行中" in statuses)
    check("没有报错状态",
          not any(s in ("出错", "找不到设备", "设备不支持") for s in statuses))
    check("引擎已退出", not eng.is_alive(),
          f"{stopped_elapsed:.1f}s" if stopped_elapsed is not None else "")
    check("退出后报告「已停止」", "已停止" in statuses)

if eng.is_alive():
    eng.stop()

print()
print("=" * 62)
if FAIL:
    print(f"结果: 失败 {FAIL}")
else:
    print(f"结果: 全部通过{'（' + str(len(SKIP)) + ' 项因已有实例而跳过）' if SKIP else ''}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
