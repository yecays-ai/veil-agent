# HANDOFF

## 工单 330（2026-11-10）：后台浏览器只能上公网

- 宿主通道的后台浏览器（Playwright MCP）现在只经 VEIL 的公网代理出网（复用 `browser_tools.PublicProxy`：只放行公网 IP 的 HTTPS 443，DNS 由代理解析并固定），并加 `--proxy-bypass <-loopback>`。点链接、重定向、脚本、iframe 都走这条路，到不了家里的内网、本机或明文 HTTP。WebRTC 用 `--config` 加 `--webrtc-ip-handling-policy=disable_non_proxied_udp`（不走代理的 UDP 关掉；云端已确认这两个参数和代理参数一起出现在浏览器进程上）。
- `host_executor.BrowserNetwork` 在 `prepare` 里启动，挂在浏览器网关上，网关退出时关闭；代理起不来就不提供浏览器（只记一条说明）。`executor_opencode.run` 无论从哪里返回（包括提前拒绝、已停止）都会对所有宿主网关调用 `__exit__`（没进入的也会关掉它带的代理）。
- 云端用 Chromium 实测：经这个代理访问 127.0.0.1 上的网页会失败（ERR_EMPTY_RESPONSE）。未验证：Lan 电脑上的 Edge。

## 工单 329（2026-11-10）：宿主机执行通道（阶段 1，默认关）

- 新文件：`veil_checkpoint.py`（4 类裁决、禁区路径、日志）、`veil_mcp_gateway.py`（包住上游 MCP，只开放白名单工具，复用 `mcp_services._Worker`）、`host_executor.py`（固定版本、上游启动命令、`prepare`、命令行开发自测 `python host_executor.py --workdir <目录> "任务"`）。`executor_opencode.run` 新增 `checkpoint`/`host_gateways`，不传时行为不变。
- 云端用真实 OpenCode 1.19.5 实测发现并处理：项目里的 `.opencode/plugin` 会在宿主机直接运行（加 `OPENCODE_PURE`、`OPENCODE_DISABLE_PROJECT_CONFIG` 等，旧宿主模式也加了）；OpenCode 的 `grep` 工具能读出 `.env`，而且它的审批请求经 HTTP 列表会 400（宿主通道关掉 grep；`grep -r`/`rg`/`cat *` 覆盖到密钥文件时检查点拒绝）；MCP 的审批请求不带参数（所以由 VEIL 网关按真实参数裁决）；拒绝一次默认会结束整个任务（开 `experimental.continue_loop_on_deny`，拒绝原因回给模型）。Playwright MCP 的 `filename` 参数能写任意文件、`browser_tabs` 带 `url` 能绕过网址检查：都拦了。
- 人优先：VEIL 不再因 Lan 的输入停整次任务；动鼠标键盘那一刻交给 Windows-MCP 自带的控制协调（区分注入与物理输入，未在真机验证）。自主醒来时不开桌面，只有后台浏览器。
- 审查（Cubic）后补上：Windows 反斜杠路径和 `$env:`/`%VAR%` 路径也按禁区检查；不往 Lan 自己的浏览器里打字，读不到前台窗口时不打字；日志不记输入的文字；`.pem/.pfx/.key` 等在宿主读取策略里也拒绝；项目里有指向外部的链接或包含 VEIL 自己的数据就不在宿主运行；网关排队有上限，排到后再裁决；非 Windows 不走宿主通道；`workbench.json` 保存时保留 `host_mode`。
- 后台浏览器点链接、被重定向时进内网的问题：工单 330 已解决（只经公网代理出网）。
- 未做（阶段 2/3）：主聊天工具收缩、删旧代码。旧容器宿主模式的 grep 仍开着（Lan 用 Docker，不受影响）。
- 下一步第一件事：远程控制在测试副本跑 5 个场景（见工单）。电脑需要 `uv`（uvx 会下载 Python 3.14 和 windows-mcp）、Node（npx 下载 @playwright/mcp）、`npm i -g opencode-ai@1.19.5`；首次下载慢，先手动跑一次预热。

## 工单 328（2026-11-10）：搜索先走 DeepSeek 官网搜索接口

- 依据：326/327 后 Lan 电脑的免费引擎仍多数失败（加速器出口被当成机器人）。DeepSeek 官网搜索接口 `POST https://api.deepseek.com/v1/tools/search`（文档 `api-docs.deepseek.com/guides/search`，用同一把 DeepSeek key，公布价每千次成功 5 元，失败/超时/空结果不收费）在 Lan 电脑经加速器实测 10 次全部 HTTP 200、每次 8 条、1.1–1.3 秒。
- 改动：`web_tools.deepseek_search`；`WebTools._find_results` 在本机 `.env` 有 `DEEPSEEK_API_KEY` 时把 `deepseek` 排在免费引擎前；通过 `Budget.reserve_amount/settle_estimate` 记账，超预算或失败就跳过；报错里替换掉 key；不跟随重定向（防止 key 被带到别的网站）；DeepSeek 始终排第一，“上次成功的引擎先试”只在免费引擎之间排序。新增测试在 `tests/test_web_tools.py`。
- 未验证：实际扣费（10 次前后 `/user/balance` 都是 8.54 元，显示精度 0.01 元，预期约扣 0.05 元），等真实搜索累计后再对账。工作台容器里的 OpenCode 不走这条搜索。

## 工单 327（2026-11-10）：浏览器失败原因进日志

- 依据：326 合并后 Lan 电脑 `tools.jsonl` 23:56:54–23:57:03 浏览器连续失败 4 次，日志只有 `Error`。`BrowserTools.invoke` 对非 `ValueError` 的异常只返回类名，Playwright 的异常类就叫 `Error`。
- 改动：新增 `browser_tools.failure_reason(exc)`：类名加异常说明的第一行，隐去本机路径、网址里的用户名密码和查询参数，最多 200 字符。新增 1 项测试；`STATUS.md` 里 326 改成已合并后的真实状态。
- 未验证：那 4 次失败的真实原因，要 Lan 更新后复现才能看到。
- 326 合并后的真实使用（`tools.jsonl` 23:52–00:00，远程控制核对）：后台浏览成功多次且没有弹窗记录；搜索三次失败两次、一次返回 7 条相关结果，搜索不稳定，不算通过；浏览器连续失败 4 次只记 `Error`。Lan 电脑开着加速器（全局模式，出口在美国机房），而且她不能关。

## 工单 326（2026-11-09）：后台浏览、搜索代理、错误原文日志

- 依据：执行层审计报告（项目共享文件 `audit/peter-execution-permission-audit-2026-11-09.md`）第 3 节（Lan 本机日志）。21:25 那次请求共 8 分 10 秒；搜索的 5 个境外引擎全部超时；改动前浏览器和工作台网关只要检测到 Lan 的任何输入就停。
- 搜索实测（2026-11-09，远程控制只读）：这台电脑（实体台式机）开着加速器（进程 FastOrange，OpenVPN TAP 网卡，0/1+128/1 全局路由），出口 IP 属于美国云机房（ipinfo：UpCloud USA）；系统代理关闭，WinHTTP 直连。`ddgs` 的 duckduckgo 超时、brave 无结果；`ddgs` 把 Bing 标为停用（指定 Bing 会悄悄改成所有引擎并发）。直接读 Bing 结果页（urllib 或无头 Chromium 都试过）2–7 秒能返回，但多词中文查询的结果大多不相关；百度、搜狗几次后出验证码；360 结果相关但每次 13–57 秒；DuckDuckGo html 版返回拦截页。结论：加速器全局模式下的机房出口 IP 被各家当成机器流量，免费抓取类搜索都不可靠；关掉加速器后的国内出口尚未实测，所以本单不加任何新引擎，只保证快速报错并写清原因；搜索质量另开工单。
- 改动：`browser_tools.py` 默认无窗口、不检查输入，`open` 加 `show`，显示模式下保留人优先；`BrowserWorkbenchGateway` 始终后台、不检查人工输入，只响应停止、暂停和上限，并去掉 `show`；主聊天正把页面显示在 Lan 屏幕上时，工作台的浏览动作一律拒绝，不碰她正在看的窗口。搜索报错里的网址会隐去用户名和密码。`web_tools.py`：`search_proxy()`（`DDGS_PROXY` 或系统代理）、上次成功的引擎排在最前、总时限 20 秒、报错写明每个引擎的失败原因。`veil_agent.py`：浏览器和桌面工具失败时，日志记录失败原文。`executor_opencode.py`：`save_run_log`（`workbench-home/runs`，保留 30 份，每份不超过 2MB），`diagnose` 带上 `veil_*` 工具的错误原文。
- 测试：全量 `unittest discover`，改动前后的失败列表相同（都是本机缺 Node、网络或 SDK 造成的旧失败），新增 2 项测试。
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
