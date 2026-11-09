# HANDOFF

## 工单 326（2026-11-09）：后台浏览、搜索代理、错误原文日志

- 依据：执行层审计报告（项目共享文件 `audit/peter-execution-permission-audit-2026-11-09.md`）第 3 节（Lan 本机日志）。21:25 那次请求共 8 分 10 秒；搜索的 5 个境外引擎全部超时。远程控制只读实测：Lan 电脑的系统代理是关的（代理软件没在运行），5 个引擎直连都在 8 秒时超时；`ddgs` 把 Bing 标为停用，指定 Bing 会悄悄改成所有引擎并发，所以不用它。不开代理时的搜索，Lan 选了“两个都要”（开代理时用免费引擎，不开时用付费接口兜底），付费接口另开工单，先报价；浏览器和工作台网关只要检测到 Lan 的任何输入就停。
- 改动：`browser_tools.py` 默认无窗口、不检查输入，`open` 加 `show`，显示模式下保留人优先；`BrowserWorkbenchGateway` 只在显示模式下检查输入，并去掉 `show`。`web_tools.py`：`search_proxy()`（`DDGS_PROXY` 或系统代理）、上次成功的引擎排在最前、总时限 20 秒、报错写明每个引擎的失败原因。`veil_agent.py`：浏览器和桌面工具失败时，日志记录失败原文。`executor_opencode.py`：`save_run_log`（`workbench-home/runs`，保留 30 份，每份不超过 2MB），`diagnose` 带上 `veil_*` 工具的错误原文。
- 测试：全量 `unittest discover`，改动前后的失败列表相同（都是本机缺 Node、网络或 SDK 造成的旧失败），新增 2 项测试。
- Lan 已定方向 A：OpenCode 跑在宿主机上，VEIL 统一裁决权限，不直接交出原始的 Windows-MCP、Playwright MCP 或不受限的 PowerShell；Docker 只用来隔离不可信代码。阶段 1 在测试副本里进行。

## 工单 325（2026-11-09）：桌面动作退回主聊天

- 现象（`data/peter/tools.jsonl` 21:25–21:30，324 合并后）：只弹一张桌面授权卡；首个工作台调用里 `veil_browser_public_browser` 4 次 error、`veil_windows_windows_launch` error，被判“桌面任务已停止或结果不确定”；同一次请求之后的工作台调用都以 `0 tool calls` 立即停止（原因未验证）。主聊天 `web_fetch`、`windows_observe`、`peek_screen` 成功。
- 设计原因：链路为主聊天 → 工作台 → Docker 内 OpenCode → MCP 回到宿主网关 → UIA，每层各有停止/超时/授权规则；错误原文没有记录；只能在 Lan 电脑真机测；两套桌面通道并存，模型失败后绕路。
- 处理（Lan 选择）：主聊天恢复三个桌面动作；`veil_agent.py` 不再给工作台传桌面网关；工作台说明改为不能操作 Windows 软件。`WindowsWorkbenchGateway` 与执行器支持保留。
- 下一步：另开工单，先加每层错误原文日志，再由开发侧在 Lan 电脑用远程控制自动跑真实链路，通过后才切换。

## 工单 324（2026-11-09）：桌面调用排队与一次授权

- 现象（Lan 正式 Peter，`data/peter/tools.jsonl` 20:51–20:59）：工作台里 `veil_windows_windows_discover_apps/list/launch` 反复 error，6 次内被判“同一调用重复多于 3 次”而停止；随后模型改用 shell 启动 notepad.exe（弹命令授权卡）。每次工作台调用重新弹一张桌面授权卡。
- 代码原因：`WindowsWorkbenchGateway.invoke` 用非阻塞锁，模型并行发出的第二个桌面调用立刻返回“搁置”（error）；授权 `authorized` 每个网关实例各自一份。
- 修复：并行调用改为排队，期间若任务已停止（未知结果会在释放锁前停止）则返回“中断”，不执行动作；一次桌面授权在同一次聊天请求内的所有工作台调用共用，下一次聊天请求重置（`tools_agent.begin_request`）。排队最多等 60 秒（等人点授权卡的时间不算），超时就停止整个任务并返回“中断”，避免客户端已放弃的调用很晚才执行桌面动作。
- 未验证：授权卡等待超过 OpenCode 的 MCP 调用等待上限是否导致报错；工作台报告里没有具体错误文本，也没有日志记录。若仍报错，需要先加错误文本日志再修。

## 当前工单 323（2026-11-09）：状态对齐与工程规则

- 范围：更新 `STATUS.md`、`HANDOFF.md`；加入 `integration-complete` 规则（`AGENTS.md`、PR 模板、`ticket` 与 `integration-complete` 技能）；`veil_agent.py` 的 `tool_schemas()` 在工作台可用且桌面观察/交互通道都启用时，隐藏 `windows_launch`/`windows_manage`/`windows_control_action`（常量 `WORKBENCH_DESKTOP_ACTIONS`，与 `WindowsWorkbenchGateway` 的动作一致）。只读观察、画布、桌面图标入口不动。测试：`tests.test_executor_opencode.ChatWiringTests`。
- 目的：之前的 STATUS 把开发侧已完成的验收也写成“待 Lan”，并保留已过期的“等待 PR #322”。现在开发验收与 Lan 体验分开写。

## 工单 322 正式使用观察（2026-11-09，来自 `data/peter/tools.jsonl` 与 Lan 截图）

- 20:07 首次：主聊天旧 `windows_*` 微动作连续出现多次“结果不确定”（`windows_control_action`、`windows_manage` 超时），文字实际已写入记事本；20:08:45 才调用 `workbench`，20:08:58 返回 `1 steps, 0 tool calls`、原因“桌面任务已停止或结果不确定”。停止原因日志里无法区分：人工输入接管、取消标志、主聊天停止。该判断未验证。
- 20:14 第二次：Peter 用 `field_trip` 浏览器和 `windows_*` 完成读页、新标签写摘要、不保存；没有 `workbench` 调用。其间 `windows_launch`、`windows_manage`、`windows_control_action` 各出现一次“结果不确定”，靠重新观察绕过。
- 在允许的软件里普通输入无需审批是 2026-10-25 的决定；只有保存、发送、覆盖确认才问。所以往已打开文档输入不弹审批符合规则，不是缺陷。
- 两次都说明旧的主聊天桌面微动作入口仍被优先使用。Lan 已决定收起，本工单 323 做了最小版本（只收动作类，保留只读观察）；其余旧入口的彻底撤除仍是审计工单四，等新通道验收后再做。

## 其他待办

- 工单 320 的三个缺口见 `STATUS.md`。
- 审计的“成果保存与安全续接”（工单三）未开始。
- 工单 318/319 无 Lan 真实体验证据。
