# HANDOFF

## 工单 339（2026-11-10）：权限分类合成一张表；去掉重复的步数/时间上限

- 依据：Lan 要求统一权限、合并上限（审计 4.1、4.2）。
- 新增 `tool_access.py`：一张表记每个工具「长任务里只读（r）/有副作用（w）/委派调查可用（d）」。`long_tasks.READ_ONLY_TOOLS`、`SIDE_EFFECT_TOOLS` 和 `delegate_agent.READ_ONLY_TOOLS` 改为从它派生，内容与改前逐项相同（`tests/test_tool_access.py`）。它不决定允许/询问/拒绝，那仍在 `tools_agent.tool_policy` 和各工具里；`tool_policy` 的九种返回值同时是发给 Peter 的「当前能力」文字，没动。
- 删除：浏览器自己的 20 步和 5 分钟上限（主聊天的步数和预算已经限制了调用次数），连同 `begin_request`；工作台的 30 步上限（`max_steps`），代理 key 的请求数改为固定的防失控值 600。工作台仍保留费用上限（唯一的硬限）、10 分钟看门狗（免费模型不会触发费用上限）和同一调用重复上限。
- 没动：主聊天的 `tools.max_steps`、自主醒来的 60 步/¥0.25/15 分钟、桌面动作 8 秒、「Lan 碰电脑 Peter 让位」。
- 未做：浏览器换 Playwright MCP、桌面换 Windows-MCP（要在测试副本对着真实场景新旧对比）。
## 工单 340（2026-11-10）：Peter 申请加预算

- 设计：不新建预算或审批系统。`Budget` 加 `raise_today`/`daily_limit`/`monthly_limit`/`headroom`（当天有效、只在内存，重启回到配置值），四处上限检查改读有效上限；`workbench` 新参数 `budget_cny` 走 `Chat._approve_run_budget`，用现有 `self.approval` 卡（网页、QQ 都能批）。
- 选择：加价只管这一次任务（单次上限）；日/月上限只在装不下时按差额提高且只限当天；自主活动不弹卡（和现有“自主不问人”一致）。
- 未做：聊天本身把当天额度用光时没有申请入口（那时模型已发不出请求），仍是 #341 的一句提示。
- 未验证：测试副本真机弹卡与 QQ 收卡。

## 工单 339（2026-11-10）：自主上下文超限与预算耗尽崩溃

- 现象：19:52 自主活动 ¥0 失败，提示"自主活动上下文超过安全限制"。`autonomy.py` 的 `MAX_CONTEXT_BYTES` 原为 12KB，只会依次删更早活动、生活记录、实验室、旅行，上一次活动（19:28 记了 60 个成功工具，约 6.5KB）和长任务状态不裁，最后直接 `raise UserError`。
- 改动：上限 32KB；上一次活动 `successful_tools` 只带最近 10 个并加 `successful_tool_count`，`unconfirmed_tools` 最多 12 个；仍超限时依次去掉 thoughts、把上一次活动换成"用 autonomy_history 读取"，不再报错。`recent_activity_context`（聊天侧，1500 字节）超长时丢弃工具列表而不是报错。
- 预算耗尽：`run_chat` 普通发送分支补 `except BudgetExceeded`（提示后继续循环），`Chat.send` 的清理分支同样处理，避免异常冒出去关掉服务。
- 验证：`tests/test_autonomy.py`、`tests/test_chat.py` 新增用例通过；其余 4 个 `test_autonomy` 旧错误在改动前就存在（本环境缺 Windows 依赖），不是本次引入。
- 未验证：Lan 电脑上下一次自主活动和预算耗尽场景的真机表现。

## 工单 338（2026-11-10）：统一权限与删重复，第一步

- 依据：Lan 要求把分散在 14 处的权限统一、删重复实现；审计 `peter-execution-permission-audit-2026-11-09.md` 里的删除项 #336 之后基本未做（主程序 37,265 行，与审计当日 37,309 行相比几乎没变）。
- 本步删除：工作台桌面网关 `WindowsWorkbenchGateway`（#325 以后无调用者）及 `executor_opencode` 里的 `windows_gateway`/`desktop_gateway`、`veil_windows` MCP 配置、`VEIL_WINDOWS_PORT` 放行规则、`desktop_stopped` 原因，对应测试。主聊天里"Lan 碰电脑 Peter 让位"和浏览器 `show:true` 让位保持原样，未改。
- 新增：白名单由 Lan 自己选。`windows_list` 里没登记但已打开的软件会带一个 `app_id` 和 `needs_lan_approval`；Peter 用它调 `windows_manage`/`windows_control_action` 时弹一张卡「同意后会记住」，选 y 就写进 `data/peter/<角色>/windows-approved-apps.json`（不动被 git 跟踪的 `windows-apps.toml`，更新不冲突；删掉某一项即撤销）。ClickOnce 软件（如 FastOrange）按 `Apps\2.0\*\<exe名>` 通配识别，不需要启动路径，Peter 不能自己启动它。授权后的动作：activate/minimize/restore/invoke/select/toggle/expand/collapse（不含 set_text）。仍然硬拦：命令行、安装程序、浏览器、凭据类程序（沿用 `is_blocked_window_exe` 与不可信路径规则）。未在真机验证。
- 设置页「更多」里新增卡片「Peter 可以操作的软件」：列出你从卡片同意过的软件，每项一个「撤销」按钮（接口 `/api/windows-apps`，撤销立即生效，没有同意过的软件时这张卡不显示）。UI 只是最小加法，等 Lan 整体重做界面时再调整。
- 全量测试：1362 个，失败项与改动前完全相同（16 失败 15 错误，均为云端沙盒环境原因）。
- 未做（等 Lan/GPT 审计方案或测试副本验收）：工具说明瘦身（Lan 已叫停）、权限合成一张表、浏览器/桌面换成 Playwright MCP/Windows-MCP、统一停止与日志、步数上限合并、Docker 常驻。

## 工单 336（2026-11-10）：删宿主通道，工作台权限用 OpenCode 原生规则

- 依据：Lan 2026-11-10 的决定：Peter 要像 Claude Code 一样自己查、读仓库、跑代码；密码和已有文件不再硬拦，每个有风险的动作弹一次授权卡，唯一硬限制是预算上限。两天的宿主通道（#329–#334）没有给她带来可见变化，整体删除。
- 删除：`host_executor.py`、`veil_checkpoint.py`、`veil_mcp_gateway.py`、`tests/test_host_path.py`、`docs/tickets/329–334`，以及 `executor_opencode.py`、`veil_agent.py`、`tool_groups.py`、`workbench_switch.py` 里只服务宿主通道的分支（`host_mode`、`host_desktop`、`host_keep_old_tools`、`host_desktop_handover`、`HOST_*`、选项 5/6）。工作台里自建的命令分类器（`_safe_project_command`、`_complex_bash_request`、`DANGEROUS`、测试脚本白名单等约 550 行）一并删除。
- 保留（对旧路径同样有用）：事件流取权限请求 `PermissionEvents`、`mcp_services` 的 cmd 参数加引号和服务 stderr 尾巴、`mcp_hidden_stdio.command_for_call`、`error_text`、`schema_top`。
- 新规则：`build_config` 写 OpenCode 原生权限：`bash` 默认 allow，`git push/clone/fetch/pull`、各类安装器、`curl/wget/npx`、`sudo` 等为 ask；`read` 敏感文件 ask；`edit` 默认 allow（日志文件仍 deny）；`webfetch`、项目外路径 ask。`decide()` 只剩一件事：对 ask 弹批准卡（自主醒来拒绝，已批准的同一请求本任务内不重复问），批准联网后仍只开放该命令用到的主机。维护任务里 git 提交/推送等仍 deny（由 VEIL 自己提交）。容器隔离、密钥代理、预算上限不变。
- 草稿 #335 关闭，因为它修的是被删的东西。

- 真机验收（测试副本 veil-agent-hosttest，真实 Docker 工作台 + 真实模型，共约 ¥0.55）：建文件并运行 Python、读回，0 张卡；拒绝 pip install 后任务继续并自己写了后备文件（加了 `continue_loop_on_deny`）；批准 pip install 可装成，但容器系统 Python 受 PEP 668 限制，模型连换 3 种命令就弹了 3 张卡（镜像问题，未处理）；读 `.env` 弹 1 张询问卡，批准后容器里仍是空文件（容器遮盖秘密文件，未改）；`web_search` 3 秒返回；可见浏览器 show:true/false 都能打开页面；旧 `workbench.json` 里残留 `host_mode` 能正常加载；`tests.test_executor_opencode` 92 项仅剩 `test_real_server_runs_a_task_through_the_program_approval` 一项，该项在 #334 之前就失败。日志在测试副本 `test-work\336`。
- 下一步第一件事：Lan 更新后做一次日常任务；另开小事让容器里的 pip 能直接装（镜像配置），并决定读 `.env` 批准后是否让容器看到真内容。
- 已知边界：`sh -c "..."` 这类包装命令是否被 OpenCode 拆开检查「未验证」；容器里联网仍由网络锁和批准卡共同限制。

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

## 工单 337（2026-11-10）

- 工单 337（草稿，未真机验收）：Peter 自己提 PR 的链路 #314 已存在；补 GitHub 令牌文件（`github-token.cmd`）与开始前的登录检查。真实 GitHub 写入仍待在测试副本验证。
