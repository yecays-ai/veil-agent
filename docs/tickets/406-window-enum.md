# 工单 406：统一窗口枚举适配层（Win32 发现 + HWND 读 UIA）

属于：B 主聊天桌面操作（Peter 能稳定操作 Windows 软件）
做完 Lan 能多做的一件事：Peter 又能看见你正在用的 ChatGPT / Chrome / QQ，自主醒来能说出你在做什么；QQ 能收发；开一个已经开着的软件不会再冒出第二个窗口；碰到卡住的窗口也不会把桌面锁死。
她怎么验收：开着 ChatGPT 和 Chrome 用一会儿，让他醒一次，看他能不能说出前台是哪个窗口；在 QQ 里让他发一条、再回一条；让他打开一个已经开着的软件，确认没有重复启动；最后开一个转圈不响应的窗口（例如卡住的大型程序），再让他看一眼，确认他没有卡死、之后还能正常看别的窗口。

## 目标

窗口发现不再依赖 UI Automation 的桌面根节点。用 Win32 `EnumWindows` 得到窗口和 HWND，再用 HWND 走 UIA 读控件；所有现有窗口工具和以后新增的窗口工具共用同一套身份来源。

## 当前状态（只读诊断结论，2026-11-20）

- 2026-11-19 12:38 系统文件批量重写之后，Python 的 UIA3 客户端（`CUIAutomation` 与 `CUIAutomation8` 都一样）`GetRootElement().FindAll(TreeScope_Children)` 只返回 0–1 个元素。
- 同一时刻同一台机器：.NET UIA2 返回 12 个、Win32 `EnumWindows` 13 个、已知 HWND 的 `ControlFromHandle` 正常（还能读子控件）。
- 已排除：权限与完整性（VEIL 后端是 Medium，同样失败）、venv 与 comtypes 缓存（全新解释器 + 重新生成的类型库仍是 0）、32/64 位、COM 单元、桌面会话、Win32 层、按句柄 UIA。
- 受影响：`windows_list`、`windows_foreground`、`windows_observe`、`windows_control_action`、`windows_manage`、`windows_launch`（会重复启动且等不到新窗口）、QQ 通道、瞄一眼（`autonomy._observe` 永远降级成“裸桌面”）、截图回退（拿不到 window_id）。
- 未证实：是否由 64 位 `UIAutomationCore.dll` 停在 `7.2.26100.1`（SysWOW64 是 `26100.9818`）引起；两份 typelib 完全相同，只读手段无法证明。本单不改系统组件。

## 本次改动

1. `WindowsDesktopBackend.list_windows` 改为 Win32 `EnumWindows`：
   - 按 z 顺序（前到后）枚举，`foreground` 用 `GetForegroundWindow` 比对，仍然是前台那一个带标记。
   - 过滤保持不变：跳过 cloaked（其它虚拟桌面/隐藏 UWP 外壳）、`Progman`/`Shell_TrayWnd`/`WorkerW`、空标题、受保护进程（密码管理器/锁屏等）、敏感标题（密码/验证码等）、未最小化且 ≤120×100 的窗口。
   - `_windows_from_rows` 统一生成窗口身份；`window_id` 公式仍是 `w-<sha256(pid|hwnd|process_path|created)>`，已存的 id、审批记录、历史日志全部兼容。
2. 读窗口仍然只走 `ControlFromHandle(HWND)`；`windows_observe`、`windows_control_action`、`windows_manage`、`windows_launch` 的代码没有改，自动跟着恢复。
3. 卡住保护（`WindowsObservationTools.invoke`）：
   - 桌面锁改由**调用线程**持有。worker 如果卡在 UIA 调用里，调用线程超时后自己释放锁，一次卡死的 COM 调用不会再永久占住桌面。
   - 放弃的读取最多 3 个；每个 300 秒后自动失效，避免一个坏窗口把观察功能永久关掉。
   - `windows_list` 只走 Win32，不碰 COM，卡住期间仍然可用。
4. `qq_channel._chat_window` 改用同一个适配层，不再调用 `GetRootControl().GetChildren()`。
5. 权限、审批、日志、截图流程都没动。

## 影响范围

- 调用者：`autonomy`（瞄一眼）、`windows_interaction`（launch/manage/control_action）、`qq_channel`、`screen_pop`（只做 `_check_default_desktop`）。
- 共享状态：`desktop_access` 的锁现在由调用线程持有；其它模块的用法不变。
- 行为差异：空标题窗口不再出现在列表里（桌面整理浮层、输入法助手等），以前 UIA 也不列它们；桌面上没有前台窗口时仍返回“无前台”，瞄一眼仍走“裸桌面 + 桌面文件名”分支。
- 新增工具：以后加窗口类工具直接复用 `list_windows` / `ControlFromHandle` / 窗口 id，不要再碰桌面根节点枚举。

## 验收步骤

- 自动：`tests.test_windows_observer`（新增 6 个：Win32 行→身份、过滤、裸桌面、卡住释放、放弃上限、QQ 适配层）、`tests.test_windows_interaction`、`tests.test_qq_channel`、全量套件。
- 真机：见开头「她怎么验收」。

## 风险与回滚

- 主要改动集中在 `windows_observer.py`，加 `qq_channel.py` 一处查找；失败可直接 revert 本 PR，不涉及数据迁移、不写系统组件。
- 已知边界：卡住的窗口会被放弃而不是等它恢复；同一次卡住期间若连续 3 个读取都卡住，观察会短暂返回“正在恢复”，等 300 秒自动解封。
