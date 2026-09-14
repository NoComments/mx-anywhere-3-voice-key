# MX Anywhere 3 滚轮后键 → 微信输入法语音输入

## 结论：可行

滚轮后面那个键（HID++ 控制 `0xC4`，wheel mode-shift / SmartShift）在
**蓝牙 LE 连接下也能被接管**。divert 之后设备会主动上报按下/松开，
程序据此注入 `Ctrl+Alt`，实现「按住说话，松手转文字」，全局所有程序可用。

## 用法

### 一次性前提
微信输入法 → 设置 → 语音输入 → 快捷键，设为 **Ctrl+Alt**。

> 本机**已经设好了**（2026-09-14 从输入法语音日志确认），无需再改。
> 换机器或重装输入法时才需要重新设一次。

### 端到端已实测通过
2026-09-14 用真机验证：按住 3 秒按键，微信输入法语音会话被正常唤起并
识别出文字。程序日志与输入法语音日志的时间戳一一对应。

### 日常使用
三种启动方式，任选一种：

| 方式 | 操作 |
|---|---|
| 桌面快捷方式 | 双击桌面上的「语音键-按住说话.bat」 |
| 原脚本 | 双击 `tools\start-voice.bat` |
| 开机自启 | 已配置好，登录后自动最小化运行，不用管它 |

启动后：**按住滚轮后面那个键说话，松手自动转文字**，所有程序通用。

停止：按 `Ctrl+C`（或直接关掉窗口），按键会自动恢复成原来的模式切换。

### 不需要管理员权限
HID++ 的全部操作（包括 `setCidReporting` divert）在**普通用户**下就能工作 ——
已在真机上确认：当前环境未提权，而所有测试（divert、读事件、注入）全部成功。

唯一的限制：注入的按键无法进入**以管理员身份运行**的窗口（Windows 的 UIPI 机制）。
普通程序（包括微信输入法）不受影响。

命令行等价：

```
python mxvoice.py                 # 正常运行
python mxvoice.py --no-inject     # 只观察事件，不注入按键（排错用）
python mxvoice.py --probe         # 只读控制表，不改任何东西
python mxvoice.py --debounce 400  # 松开去抖时长（毫秒，默认 250）
python mxvoice.py --seconds 60    # 跑 60 秒后自动退出
```

## 原理

### 为什么普通钩子抓不到这个键
该键默认执行的是**固件本地动作**（切换滚轮棘轮 / 无阻尼模式），
不产生任何主机可见的输入事件 —— 所以 `WH_MOUSE_LL` 之类的鼠标钩子
永远看不到它。这也是它必须走 HID++ 的原因。

### 接管方式：HID++ 2.0 divert

```
0x1b04 REPROG_CONTROLS_V4   （本机在 feature index 9）
  func 0  getCount                  -> 控制表行数
  func 1  getCidInfo(index)         -> 某一行：cid / task / flags
  func 2  getCidReporting(cid)      -> 当前上报状态
  func 3  setCidReporting(cid, ...) -> 修改上报状态
```

执行 `setCidReporting(0xC4, diverted=True, analytics=True)` 之后，
设备停止执行固件动作，改为上报：

```
按下   fn=0  11 ff 09 00 00 c4 00 ...   divertedButtons，payload[0:2] = cid
       fn=2  11 ff 09 20 00 c4 01 ...   状态字节 payload[2] = 1
松开   fn=0  11 ff 09 00 00 00 00 ...   全 0，表示没有任何键处于按下
       fn=2  11 ff 09 20 00 c4 00 ...   状态字节 payload[2] = 0
```

一次物理按键会产生上面 **4 个包**（fn=0 和 fn=2 各一对），程序用
`held` 标志去重。

### 最大的坑：把「请求回显」当成按键事件

同一个 HID collection 上打开的**所有句柄都会收到彼此请求的回复**
（广播投递）。回显和真事件长得几乎一样，只差第 4 字节的低 4 位：

```
回显  11 ff 09 2a 00 c4 01 ...   fn=2  sw=0xA   <- 我们自己请求的回复，丢弃
事件  11 ff 09 20 00 c4 01 ...   fn=2  sw=0x0   <- 设备主动上报，处理
```

**只认 `sw == 0x00`。**

本项目曾因为没有这个过滤，把 `getCidReporting` 的回复当成了按键事件
（一次轮询 = 一个"按键"），进而得出"蓝牙下收不到通知、这条路走不通"的
错误结论，白绕了很久。`parse_event()` 现在强制这个过滤。

### 去抖

设备在按住期间可能反复上报按下/松开。若不加处理，「按住说话」会被
反复打断。程序的做法是：收到松开后**不立即释放**按键，而是等
`--debounce`（默认 250ms）；如果这期间又收到按下，就取消这次释放，
把整个按住视为一次连续按压。

### 注入

用 `SendInput` 注入**左** Ctrl + **左** Alt。

注意**不要**加 `KEYEVENTF_EXTENDEDKEY` —— 那个标志的作用是"选右手那个键"，
会把 Ctrl 变成右 Ctrl、**Alt 变成右 Alt（AltGr）**，快捷键基本认不出来。
不加才是标准的左 Ctrl / 左 Alt（键盘钩子看到的是 `vk=0xa2` / `0xa4`）。

## 排错

| 现象 | 处理 |
|---|---|
| 提示找不到设备 | 鼠标是否已连接/唤醒 |
| 提示"已经在运行" | 已有一个实例在跑；关掉它。若是被强杀留下的残留，跑 `restore.py` |
| 按了没反应 | 用 `--no-inject` 跑，看日志里有没有 `DOWN` / `UP` |
| 按住说话被反复打断 | 调大去抖：`--debounce 400` |
| 退出后滚轮模式切换失灵 | 运行 `python restore.py` 清除残留的 divert |

### 怎么判断到底是"按键没读到"还是"输入法没响应"

两边的日志都很好读，先看哪一边断的：

1. **程序这边**：`python mxvoice.py` 窗口里有没有 `DOWN` / `UP`。
   没有 → 是按键没读到（鼠标连接？被别的程序占用了？）。
   有 → 按键这一半没问题，往下看。

2. **输入法这边**：看语音诊断日志
   `%LOCALAPPDATA%\Temp\WeTypeVoiceDiagnostic_<pid>.log`
   - 出现 `VoiceCompositionTrace start` → 输入法**收到快捷键了**，在录音
   - `emit utf8_len=N` 且 N 在增长 → 正在识别出文字
   - `abort reason=finished_without_text` → 会话起来了但没捕获到语音，
     通常是**按得太短或没说话**，不是快捷键失效

   如果这个日志在按键时**完全没有新行** → 才是快捷键没生效，
   需要去输入法设置里把语音快捷键设成 `Ctrl+Alt`。

## 文件

| 文件 | 用途 |
|---|---|
| `mxvoice.py` | 主程序 |
| `start-voice.bat` | 启动脚本（GBK 编码，无需提权） |
| `restore.py` | 清除残留 divert，恢复按键原状 |
| `make_launchers.py` | 重新生成三个启动器（GBK 编码） |
| `hidpp.py` | 零依赖的 HID++ 2.0 实现（ctypes） |
| `verify.py` | 双按钮对照实验 |
| `holdtest.py` | 测量按住时长，判断是否会自动重复上报 |
| `injecttest.py` | 用键盘钩子验证 Ctrl+Alt 注入 |
| `listen.py` | 带 sw 过滤的纯净监听器 |

启动器分布在三处：
`tools\start-voice.bat`、桌面「语音键-按住说话.bat」、
启动目录 `MX语音键.bat`（登录自动运行）。
改动主程序路径后，跑一次 `python make_launchers.py` 就能全部重新生成。
