# 工单 239：VEIL 通用电脑执行与权限架构调查

调查日期：2026-11-07（北京时间）；最新 `main` 基准：`473dabbf8c9400d200b490fcb5177da928f902b0`。执行架构源码基准为其上一项相关合并 `817de3c67e0579df96ae681f5fd583d1c0444d41`；两者之间的 PR #295 只改桌面 UI、前端测试和预览文档，没有改变本报告的执行调用链。

性质：全仓只读架构调查和迁移决策。没有读取 Lan 的人设、聊天、记忆、密钥、真实项目内容或费用账本；没有启动正式 VEIL、执行付费模型、开放真实项目、改运行配置、重构或删除执行代码。

## 1. 给 Lan 的结论

**不要把 VEIL 重写成另一个电脑 Agent，也不要让 OpenCode 直接获得 Windows 权限。最短可行路线是：VEIL 继续做 Peter 本人和总控制器；OpenCode 只做容器内的连续任务循环；浏览器和桌面能力以受限适配器接到同一个 VEIL 执行网关。**

这样 Peter 的主聊天只需调用一次“执行任务”，内部执行器可以连续读文件、改代码、跑测试、浏览网页或操作已授权软件；每个微动作不再回到主聊天模型。VEIL 在每次真实副作用发生前仍按角色、任务、资源和风险检查权限，并统一停止、预算、状态、日志和输出。

关键判断：

- **OpenCode 保留，但只当可替换的内部循环。** 它已经解决代码/文件/命令任务的连续执行；官方也明确它没有安全沙盒，必须留在 Docker/VM 内。它不能成为 VEIL 的权限裁决者，也不应接管人格、记忆、聊天、自主调度或长期任务。
- **浏览器优先复用 Playwright MCP 的动作与可访问性快照，不继续逐项扩写 `browser_tools.py`。** VEIL 只开放经过筛选的导航、读取、点击、输入等能力；任意 JavaScript、上传、下载、Cookie/存储、内网访问和已登录资料不能随 MCP 原样放开。
- **Windows 桌面先复用现有安全边界，再做 UFO² 动作层原型。** 当前 `windows_observer.py` / `windows_interaction.py` 已有窗口身份、敏感窗口、控件新鲜度、人工接管和动作后确认，不能提前删除。UFO² 的 UIA/Win32/COM 与批量动作值得借用，但整套 HostAgent、AppAgent、RAG、记忆和状态机与 VEIL 重叠，不采用。
- **本工单不确认任何旧执行工具现在可删。** 先把同一真实场景迁到新入口，完成 API 模型、Claude Code 通道、正常聊天、自主醒来和 Windows 真机验收，再删除被完整接管的旧入口。调用者存在不等于有产品价值，代码多也不等于能删。

优先做的第一项是 **工单 240：把已合入的工作台收敛为容器必需、模型无关的正式项目执行入口**。它会立即让 Peter 在一次主工具调用里完成“找到已授权项目 → 批量读代码 → 修改 → 跑测试 → 普通报错自修”，同时建立后续浏览器和桌面执行器共用的运行、授权和结果契约。

## 2. 证据边界与 237/238 真实状态

| 项目 | 已确认 | 不能扩大的结论 |
| --- | --- | --- |
| 代码基准 | 最新 `origin/main` 为 `473dabb`；执行架构相关提交 `817de3c` 明确为 PR #294 合并提交；新增 PR #295 不改执行源码 | PR #294 原标题写“不合并”已过时，不能继续当当前状态 |
| 237/238 合入 | `executor_opencode.py`、`workbench_scope.py`、Dockerfile、27 项测试及验证脚本都在 `main` | “代码在 main”不等于默认开启或已开放真实项目 |
| Windows 证据 | 工单记录确认 Docker Desktop 隔离二次探测、真实模型整套任务、测试副本 Peter 聊天通过 | 未验真实项目、自主醒来、Linux 宿主机；容器仍可联网 |
| 自动检查 | PR #294 审查修正记录和合并提交可核对；GitHub 连接器没有返回该合并提交的状态检查 | 本报告不声称 PR #294 CI 全绿 |
| 当前机器 | 本轮 PATH 找不到 `opencode`，且角色配置没有启用 `[executor]` | 本轮不能重跑真实 OpenCode；不把历史 Windows 报告冒充本轮实测 |

证据分为四类：源码已确认、历史自动检查、历史 Windows 实测、本轮未验。后文不会互相替代。

## 3. 为什么现在“打开、点击、滚动”会消耗主 Agent 步数

普通 API 聊天的真实路径是：

```text
Lan 的一句话
  → Chat._tool_chat（主模型第 1 次请求）
  → 一个或多个 VEIL 工具调用
  → 工具结果塞回同一上下文
  → Chat._tool_chat（主模型第 2 次请求）
  → 下一批工具调用
  → ……直到回复或达到上限
```

这不是 Playwright 或 UIA 本身要求的限制，而是 VEIL 目前把微动作直接暴露给主聊天模型造成的。

| 限制 | 源码位置 | 当前效果 |
| --- | --- | --- |
| 正常聊天模型轮数 | `veil_agent.py:4029-4097`；Peter 配置 `tools.max_steps = 20` | 每次观察后的新决策至少再占一次主模型请求；最后一轮不再提供工具 |
| 自主活动动作数 | `veil_agent.py:4030-4089`；`autonomy.max_tool_steps = 60` | 每个工具调用计一步；免费回顾有例外，但桌面/浏览器动作没有合并成任务 |
| 自主活动费用 | `autonomy.run_budget_cny = 0.25`，共享日额度 ¥3 | 连续任务可能先碰本次费用上限，而不是先完成用户目标 |
| 自主活动时间 | `autonomy.py:15`、`1014-1087`，15 分钟 | 只能在检查点协作停止；正在执行的外部调用不一定即时被抢占 |
| 独立浏览器 | `browser_tools.py:16-18`、`292-334`，20 动作/5 分钟 | `open`、`click`、`scroll`、`observe` 每次仍先作为主工具往返；内部上限又叠一层 |
| Windows 桌面 | `windows_observer.py` + `windows_interaction.py` | `list/foreground → observe → action → observe` 是多个主工具步骤；安全校验完整，但没有任务内循环 |
| 工具结果长度 | `veil_agent.py:4000-4027`、`tools.max_result_bytes = 8192` | 长页面或多文件结果会被截断/省略，再读下一段又占一步；不适合作为长任务主存储 |
| Claude Code 通道 | `claude_agent_channel.py:30-35`，30 轮/80 工具/15 分钟 | 它内部可连续执行，但使用另一套原生工具、拒绝规则、预算记录与审批桥；不是统一基础设施 |
| 委派调查 | `delegate_agent.py:12-13`，6 轮/12 只读工具 | 只读研究子循环，不能修改或执行，不能替代通用执行器 |
| OpenCode 工作台 | `executor_opencode.py:31`、`325-449`，30 步/10 分钟 | 唯一已经做到“一次主工具调用，内部连续执行”的正式路径；目前覆盖项目文件、命令和 OpenCode `webfetch`；`webfetch` 会经权限回调，但当前被 VEIL 自动放行，容器网络也未隔离 |

结论：应保留总任务的费用、时间、动作和人工停止硬上限，但把“一个按钮/一次滚动”从**主 Agent 上限单位**改成**执行器内部事件**。主 Agent 只为任务开始、跨边界授权、重要中间结果和最终交付付出回合。

## 4. 当前全部执行入口与重复实现

### 4.1 主循环直接提供的能力

| 能力 | 实现与调用者 | 独立价值 | 当前重复/问题 |
| --- | --- | --- | --- |
| 工作区文件 | `tools_agent.py` 的 filesystem MCP 包装；`Chat._execute_tool_call` | 小文件快速读写、Lab 边界、旧版本保留 | 与 OpenCode 的 read/edit/shell 重叠；每段写入/读取占主回合 |
| Lab Python/预览/游戏 | `lab_sandbox.py`、`lab_preview.py`、game manager | 安全试验和可视结果是 VEIL 产品能力 | 代码运行与工作台重叠，但 Lab 展示/生命周期不能直接删 |
| 搜索/抓取/研究 | `web_tools.py` | 快速、低成本的只读发现，不需要浏览器 | 不能取代交互网页；应作为执行器的只读搜索适配器保留 |
| 独立浏览器 | `browser_tools.py`（已有 Playwright 1.64.1） | 公网 HTTPS、无登录资料、DNS 固定、GET/HEAD、禁下载 | 自研动作面过窄；微动作回主循环；危险词正则不是通用权限模型 |
| GitHub 只读 | `github_readonly.py` 经内置 MCP | 仓库搜索、目录和 README 的结构化 API 效率高 | 与工作台/git/web 有覆盖，但仍有独立任务价值，不能按重复删除 |
| Windows 观察/交互 | `windows_observer.py`、`windows_interaction.py`、`windows_discovery.py` | 真实程序边界、敏感窗口、控件身份、人工优先、结果确认 | 观察和动作被拆成多个主步骤；策略散在多个模块 |
| 桌面文件 | `desktop_files.py` | 只读桌面与 Peter 创建文件的不同范围 | 与通用文件执行重叠；未有等价授权/恢复前不能删 |
| MCP 服务 | `mcp_services.py` | 外部服务可插拔；Nowhere/GitHub/小红书有业务价值 | 权限按整个服务 `direct/ask`，不是按操作/资源；与各本地策略体系不同 |
| OpenCode 工作台 | `executor_opencode.py`、`workbench_scope.py` | 连续项目执行、快照、容器、密钥代理 | Dockerfile 固定在 `opencode-ai@1.19.5`，宿主 PATH 模式不校验版本；非容器路径仍可越界；权限仍靠独立正则和 OpenCode 规则 |
| Claude Code 通道 | `claude_agent_channel.py` / bridge | 可作为聊天模型通道，利用订阅模型 | 同时保留 Bash/Read/Edit/Web 等另一套执行面与权限，长期必须收敛 |
| 委派只读 Agent | `delegate_agent.py` | 降低主模型调查负担，可换独立模型 | 只读白名单与执行权限分开，保留；不扩成第二个执行核心 |
| 邮件/QQ 后台通道 | `away_bridge.py` 调用 `email_channel.py` / `qq_channel.py` 的 `reply`，不经过 `Chat._execute_tool_call` | 远离电脑时收发消息、转发审批回复和 Peter 主动联系 Lan | 后台线程直接产生外部发送副作用；邮件/QQ 各自维护每日限额、目标校验和发送错误，必须纳入统一停止、run 日志、预算与角色隔离验收，但其业务保护不能删除 |

### 4.2 当前至少七套权限判断

| 位置 | 判断依据 | 可实际阻止什么 | 不能声称什么 |
| --- | --- | --- | --- |
| `tools_agent.py:39-105` | 工具名集合 `ALLOW/ASK/DENY/LAB_ONLY` | 未登记工具、Lab 路径、文件写入入口 | 不能表达一个任务对某项目/域名/应用的持续授权 |
| `mcp_services.py:544-654` | 整个服务 `direct/ask` | 服务停用、每次服务调用审批 | 内置服务全部直接放行；第三方服务没有工具级风险分类 |
| `executor_opencode.py:35-114` | 命令/路径正则 + OpenCode permission | 已识别部分危险命令、外部目录和显式联网/安装/推送请求 | `SAFE_COMMANDS` 自动放行测试命令，但测试代码本身可联网、安装、推送或触发其他副作用；`webfetch` 也被 VEIL 自动放行。不能声称这些副作用都经过审批；正则和 OpenCode permission 也不是沙盒 |
| `browser_tools.py:19-38` | URL/链接文字危险词、GET/HEAD、公网 IP | 内网、下载、登录/购买等明显入口 | 不能扩展到通用表单和登录态；词匹配会误拦/漏拦语义 |
| `windows_interaction.py:389-424`、`1217-1610` | 应用/动作白名单、控件元数据、敏感标签 | 未授权程序、危险控件、保存/发送/提交审批、控件变化 | 审批仍按一次动作，不能给一段可撤销任务持续授权 |
| `claude_agent_channel.py:39-56` | Claude 原生工具 allow/deny rules + 审批桥 | 明确的 secret 路径、原生工具询问 | 与 VEIL 文件/工作台/桌面策略不是同一个裁决结果 |
| `away_bridge.py:86-191`、`email_channel.py:175-280`、`qq_channel.py:256-301` | 通道来源、审批回复关联、每日发送上限、固定联系人 | 只向 Lan 的配置目标发送、限量、忙碌/停止时不继续取件 | 发送不经过 `Chat._execute_tool_call`，未共享执行 run、费用或统一操作裁决；已发出的邮件/QQ 不能撤回 |

这些规则有些互补，不应粗暴合成一张字符串白名单。要合并的是**裁决入口和授权记录格式**，各适配器仍负责把真实操作解析成结构化资源与风险事实。

## 5. 成熟开源方案调查

调查优先看项目源码/安全声明/许可证，再用 issue 和社区反馈找真实失败模式。Stars 只证明关注度，不证明适合 VEIL。

| 方案 | 维护/许可证与真实能力 | 判断 |
| --- | --- | --- |
| [OpenCode](https://github.com/anomalyco/opencode) | 活跃、MIT；提供 `opencode serve` HTTP API、文件编辑、shell、LSP、MCP、模型适配和内部 Agent 循环。官方[安全声明](https://github.com/anomalyco/opencode/security)明确 permission 是 UX，不是隔离，真正隔离要 Docker/VM。近期 issue 仍有[审批等待重启丢失](https://github.com/anomalyco/opencode/issues/36347)、[权限原子准入缺口](https://github.com/anomalyco/opencode/issues/61645)等。 | **继续用，但只放容器里，并由 VEIL 做最终授权。** 适合项目/命令执行和承载受限 MCP，不适合取代 VEIL 核心或安全边界。固定已验版本并做契约测试，不能跟随任意本机版本。 |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Microsoft 活跃维护，Apache-2.0；结构化可访问性快照、导航、点击、输入、选择、标签页和隔离/持久资料。官方明确[不是安全边界](https://github.com/microsoft/playwright-mcp#security)，还提供 RCE 等价的 `browser_run_code_unsafe`；社区 issue 指出默认无任务级速率限制、SSRF/任意脚本风险。 | **动作层直接复用，能力层必须裁剪。** 第一阶段只接隔离 profile 和公开网络；禁用任意代码、evaluate、上传/下载、存储、网络 mock、WebMCP 和扩展连接。后续登录态按独立资料与域名/动作授权开放。 |
| [UFO²](https://github.com/microsoft/UFO/tree/main/ufo) | Microsoft、MIT、LTS 声明；UIA + Win32 + COM、视觉回退、MCP 动作层和多动作验证。其架构也包含 HostAgent、每应用 AppAgent、RAG、经验记忆和 FSM；文档仍把 PiP 虚拟桌面列为 coming soon，安全页有 2026 年多项命令/网络公告。 | **只做隔离原型，不整套引入。** 优先评估 AppPuppeteer/UIA action executor 是否可单独复用；不引入 HostAgent、AppAgent、Knowledge Substrate、Galaxy 或它的长期记忆。安全仍以 VEIL 白名单和程序复核为准。 |
| [pywinauto](https://github.com/pywinauto/pywinauto) | BSD-3-Clause，约 6.4k stars，仍有 2026 提交；成熟 Win32/UIA 包装。社区真实经验也说明 UIA 对标准控件高效，但 Electron/Flutter/自绘控件常需视觉回退。 | **可作为 UFO 过重时的动作层备选。** 它是库，不是任务循环、权限或隔离系统；不会单独解决主回合消耗。当前 `uiautomation` 已能完成的部分不为换库而重写。 |

没有选择的方向：

- 不采用 browser-use/另一套完整网页 Agent：Playwright MCP 已覆盖动作层，另加完整浏览器 Agent 会制造第二个循环。
- 不把 MCP 当权限系统：MCP 只是传输/工具描述协议；远端或本地 server 都必须经过 VEIL 裁决。
- 不依赖提示词声明“不要碰密钥”：文件、网络、桌面和服务边界必须由进程/容器/适配器实际阻止。
- 不直接连接 Lan 日常浏览器 profile：页面提示注入、Cookie、下载和已登录动作会把风险从“公开浏览”扩大到个人账户。

社区反馈只作为风险线索：OpenCode 有真实用户报告循环和 token 消耗异常；桌面自动化使用者普遍建议 UIA 优先、视觉只作回退。最终判断以上游安全声明、源码边界和 VEIL 自己的 Windows 验收为准。

## 6. 目标结构：一个 VEIL 执行网关，多个可替换适配器

```text
Peter（任意 API 模型或 Claude Code 聊天通道）
                │ 一次 execute_task(goal, requested_scopes)
                ▼
VEIL Execution Gateway
  ├─ Run：role / task / state / deadline / step-cost budget / cancel token
  ├─ Grant：resource + operations + risk + expiry + one-run binding
  ├─ Policy：allow / ask / deny（每次副作用前重新检查）
  ├─ Event log：结构化动作、结果、费用、审批、停止原因
  └─ Artifact：长报告、文件清单、截图引用；主聊天只收摘要和引用
                │
                ▼
OpenCode task loop（Docker，版本固定，无宿主机目录/密钥）
  ├─ workspace adapter：项目文件、shell、测试、Git
  ├─ browser adapter：筛选后的 Playwright MCP
  ├─ desktop adapter：现有 VEIL UIA，后续可换 UFO²/pywinauto 动作层
  └─ service adapters：只暴露该任务已获授权的 MCP/API
```

### 6.1 责任边界

| VEIL 永久负责 | 执行器负责 | 适配器负责 |
| --- | --- | --- |
| 人格、角色隔离、记忆、聊天历史、主动唤醒、长期任务、用户停止、总预算、审批卡、授权记录、运行状态、审计与结果交付 | 在一个目标内规划多步动作、观察结果、普通错误恢复、形成最终报告 | 把具体动作解析为资源/操作/风险；实施 OS/网络/应用边界；执行前后确认真实状态 |

OpenCode 是默认实现而不是协议本身。`Execution Gateway` 只依赖“启动、事件、审批、取消、结果”的适配器契约；以后若替换 OpenCode，不改 Peter 的人格、记忆、权限或聊天。

### 6.2 主 Agent 如何选择

- 简单对话、记忆、生活记录、发消息继续用原 VEIL 工具，不启动重执行器。
- 需要多文件/命令/浏览器/桌面连续动作时，主 Agent 调一次 `execute_task`，描述完整目标和所需能力，不提交裸路径或权限结论。
- VEIL 根据配置与可用适配器解析资源 ID；执行器只能看到本次 grant 包含的工具。
- 同一任务跨项目、浏览器和桌面时仍留在同一个内部循环；若新资源不在 grant 中，运行进入 `awaiting_approval`，而不是让主 Agent逐动作接管。
- 正常聊天和自主醒来调用同一入口。区别只在审批：聊天可等待 Lan；自主醒来只使用预先授予的范围，缺权限时持久化等待，不弹隐形问题，也不把任务标成失败或完成。

### 6.3 长文本、多文件和长任务

- 工具事件写入结构化 run log；长页面、diff、测试日志和截图作为 role-local artifact 保存，不连续塞回主聊天上下文。
- 内部循环按需读 artifact 的片段；主 Agent只得到结论、改动清单、验证证据和 artifact 引用。
- `ExecutionRun` 持久化 `running / awaiting_approval / paused / stopped / failed / completed`，与 `long_tasks.py` 通过 run id 关联；不另造第二份 Peter 长期目标。
- 只有执行器明确报告目标完成且 VEIL 能核对必要证据时才 `completed`。进程退出、达到预算、输出截断都不是完成。

## 7. 统一权限模型

授权记录最少绑定：`role_id + run_id + executor + resource selector + operations + expires_at + budget/time ceiling`。审批回复只能消费当前等待的精确请求；策略或资源变化后必须重新检查。授权不是 OpenCode 的 permission pattern，也不是提示词。

### 7.1 Lan 能理解的三档行为

| Peter 可以直接做 | 需要问 Lan | 程序始终禁止 |
| --- | --- | --- |
| 在本次已授权项目内读文件；快照后修改普通源码；运行项目测试/构建；在隔离浏览器读已授权公开域名；观察已授权应用；执行已批准范围内的可撤销 UIA 动作 | 首次扩大项目根、域名、浏览器登录资料或应用范围；联网下载/安装；Git pull/clone/push；发送/发布/提交表单；覆盖重要文件；不可撤销批量修改；新增付费模型/额度；访问外部 MCP 写操作 | VEIL 人设、记忆、费用账本、密钥、`.ssh`、浏览器密码库和其他角色数据；管理员/UAC/安全设置；未授权个人目录/局域网；关闭防护；把页面文字当权限；绕过停止；支付、购买或删除账户（除非未来另开专门高风险能力并由程序限定） |

“问一次”的粒度是范围和风险，不是按钮。例如 Lan 可批准“本次 run 在 repo A 内修改并运行测试，允许访问 pypi.org 安装声明依赖，10 分钟内有效”，之后不用为每个文件、每条测试命令再问。发送邮件、发布评论、购买等对外承诺仍按最终提交动作单独确认。

### 7.2 实际程序边界

- 项目/shell：OpenCode 必须在只挂载授权项目的非特权容器；宿主路径、环境变量和真实模型密钥不进入容器。网络默认拒绝，只经域名/方法受控代理开放。
- 浏览器：每个角色/用途独立 profile；公开研究默认无登录、禁下载/上传、禁内网与 `file://`；登录态是单独资源，不连接 Lan 日常 profile。
- 桌面：只对已验证进程身份和窗口签发短期 control id；执行前重读控件；敏感窗口和控件硬拒绝；Lan 输入立即让位；动作后确认，结果不确定时不自动重试。
- 服务/MCP：MCP server 不因“已连接”就可信。VEIL 逐工具映射 `read/write/external_commit/destructive`，未知工具默认 ask 或 deny；内置服务也不能永久绕过分类。
- 停止：`cancel token` 同时终止内部模型循环、容器进程树和正在等待的适配器动作；已经发出的外部请求不能谎称撤销，只记录“可能已发生”。
- 费用：主聊天与执行器分账但共用总账；运行前预留、过程中按事件结算、缺用量按上限计；执行模型选择和预计日费用变化必须先由 Lan 决定。

## 8. 代码保留、合并、替换、删除清单

### 8.1 保留

| 源码 | 保留价值 | 依赖/影响 |
| --- | --- | --- |
| `veil_agent.py` 的角色加载、Chat 上下文、Budget、消息持久化 | Peter 身份、模型无关聊天和共享费用核心 | 后续只把重执行从 `_tool_chat` 移到网关，不重写聊天 |
| `memory_*`、`profile_notes.py`、`life_records.py`、`thoughts.py` | VEIL 独有长期关系、角色隔离和生活状态 | 执行器只接收完成任务所需最小上下文，不直接读库 |
| `autonomy.py`、`long_tasks.py` | 主动醒来、聊天让位、持久任务 | 与 ExecutionRun 关联，不再自己维护另一套执行动作规则 |
| `web_agent.py` 审批卡、停止、事件发布 | Lan 可见控制面 | 改成展示 run/grant/event；保留停止优先级 |
| `workbench_scope.py` 快照/还原 | 修改前留退路 | 修复忽略文件/暂存状态边界后可成为 workspace adapter 的恢复层 |
| Windows 保护与确认逻辑 | 当前最具体的实际程序边界 | 换 UIA 库也必须保留等价测试和行为 |
| `web_tools.py`、GitHub/小红书/Nowhere 等业务适配器 | 快速搜索和专门业务语义 | 可被执行器调用，不因“浏览器也能打开”删除 |
| 消息、邮件、QQ、贴图、屏幕弹出 | 对外沟通和 Peter 表达能力，不是通用电脑控制的重复实现 | 外部承诺继续有各自业务限制 |

### 8.2 合并

| 当前分散位置 | 合并目标 | 删除条件/兼容影响 |
| --- | --- | --- |
| `tools_agent.tool_policy`、`mcp_services.permission_for`、OpenCode 正则、浏览器危险词、Windows 审批、Claude deny rules | `ExecutionPolicy.authorize(operation, resource, run, role)` | 不是删各适配器校验；先让全部执行路径写出相同 operation/resource，再移除重复裁决 |
| Chat/autonomy/browser/workbench/Claude 的步数、时间、重复、费用上限 | `ExecutionRun.limits` + 适配器局部资源上限 | 保留截图数、网络字节等局部上限；删掉含义相同的总任务计数 |
| `tools.jsonl`、activity、workbench summary、Claude cost log | run event + shared ledger + UI 投影 | 迁移脚本/兼容读取验证后不长期双写 |
| chat stop、autonomy yield、browser stopped、OpenCode kill | 单一 cancel token | Windows 真机证明停止传播到所有子进程/动作后删除旧分支 |

### 8.3 替换

| 旧实现 | 替代 | 何时可移除旧入口 |
| --- | --- | --- |
| `browser_tools.py` 的五动作模型接口 | 筛选后的 Playwright MCP adapter + 内部任务循环 | 公开阅读、动态页面、表单（不提交）、错误恢复、停止和网络边界全部实测后 |
| Windows `list/observe/action` 直接给主聊天 | 作为 desktop adapter 给内部循环；动作层可评估 UFO² AppPuppeteer/pywinauto | 现有应用验收矩阵全部通过、敏感窗口/人工接管/动作确认等价后；底层保护不因入口替换删除 |
| 普通代码任务的逐文件工具和 `run_lab_python` | container workspace adapter | 新建/已有项目、二进制/长文件、测试、失败恢复、快照还原、停止和费用验收后；Lab 预览与作品展示另行保留 |
| Claude Code 原生 Bash/Edit/Web 执行面 | 同一个 `execute_task` 和 VEIL adapters | Claude 通道可完成同样项目/网页/桌面任务且审批一致后；聊天模型本身保留 |

### 8.4 删除

**现在：不删。** 这次没有确认无调用者且无独立价值的生产执行模块。

未来可删的只包括：已迁移场景的旧模型 schema、旧分发分支、重复总任务限额/审批适配代码，以及完全被官方动作层覆盖的自研 Playwright 操作代码。删除 PR 必须提供：旧入口全局调用者清单、新旧同场景 Windows 证据、回滚方式、配置迁移、API 与 Claude 两通道、聊天与自主醒来回归。永久双写或永久兼容层不保留。

## 9. 分阶段实施与真实体验验收

### 工单 240（P0）：项目执行入口生产化

最小完整范围：

1. 把 `workbench` 收敛为统一 `execute_task` 的第一个 workspace adapter；只允许固定版本 OpenCode + Docker，删除“启用后可无容器运行”的生产选项。
2. 增加 role-local `ExecutionRun`：状态、scope grant、预算/时间/步数、取消、事件和长结果 artifact；先只支持 workspace，不预造浏览器/桌面实现。
3. 真实模型密钥继续只在 VEIL 代理；容器网络默认拒绝，按本次 grant 的域名/操作开放。`webfetch` 和测试进程的网络也必须受同一边界控制，不能再以 `SAFE_COMMANDS` 自动放行测试命令来推定测试代码无副作用。联网、安装、拉取、推送按任务范围一次授权；发布仍最终确认。
4. API 聊天与 Claude Code 通道都只通过同一个 VEIL tool schema 调用；执行模型单独显式配置，默认关闭，费用变化先让 Lan 决定。
5. 旧文件/Lab 工具不删；只记录同场景对照，为后续删除建立证据。

Lan 的实际验收：说“找到 X 项目，研究代码，运行它；有问题自己修”。Peter 自己选中已授权项目、留快照、连续读改测，普通失败自己恢复；主聊天只出现一次执行任务和最终结果。读取项目外文件、密钥、其他角色数据均失败；恶意测试代码和 `webfetch` 在没有网络 grant 时也无法联网；按停止后没有继续命令；恢复快照能回到修改前。自主醒来遇到未授权联网时保存为等待，不擅自执行也不丢任务。

立即价值：这正是当前已实现最多、Windows 证据最接近完整的一条路径；先把 237/238 从默认关闭原型变成可安全验收的正式能力，比先重写桌面或另换框架更短。

### 工单 241（P1）：公开浏览任务执行器

- 接官方 Playwright MCP 固定版本，只开放隔离 profile 的受限工具；复用现有公网 DNS/IP、无下载和人工优先边界。
- 一次任务内部连续搜索、导航、点击、滚动、读取动态页面；结果写 artifact。
- 验收：“研究这个软件怎么用，自己试一试”，Peter 在公开站点连续完成，不因 20 个主工具步骤停下；内网、登录、下载、上传、任意 JS 仍被程序拒绝。

完成后才删除 `browser` 旧五动作 schema；`web_search/web_fetch` 仍保留。

### 工单 242（P1）：Windows 桌面任务执行器原型

- 先把现有 observe/action 作为内部 adapter，证明“主回合减少”与“安全边界不退化”。
- 在同一批真实应用上对照 UFO² AppPuppeteer 与当前 `uiautomation`；只有更少代码且保护可移植时才换底层。
- 验收：让 Peter 研究一个普通 Windows 软件，连续完成打开、观察、切换、填写非敏感表单、保存前询问；Lan 动鼠标立即让位，密码/UAC/安装/删除始终拒绝。

### 工单 243（P1）：统一服务权限与运行记录

- 把 MCP `direct/ask` 升级为工具/操作/资源风险映射；未知写操作默认 ask/deny。
- Chat 和自主醒来共用同一 grant；活动页展示 run、scope、费用、等待原因和停止结果。
- 验收：同一授权在一次任务内不重复问；任务结束/撤销后不能复用；角色切换不能继承。

### 工单 244 起（P2）：逐场景替换和删旧代码

按 workspace → browser → desktop 顺序，每项新能力完成真实 Windows 验收后，独立 PR 删除对应旧 schema、分发和重复限制。GitHub 搜索、社交、记忆、消息、Life、Lab 展示等有独立产品价值的能力不因代码行数删除。

## 10. 对七项验收要求的回答

| 要求 | 结论 |
| --- | --- |
| 现状清楚 | 微动作直接暴露在 `Chat._tool_chat`，叠加正常 20 轮、自主 60 动作/¥0.25/15 分钟、浏览器 20 动作/5 分钟和输出长度限制 |
| 方向明确 | VEIL Execution Gateway + 容器 OpenCode 内循环 + 受限 Playwright/Windows adapters；不是“再加 MCP 工具” |
| 权限完整 | 已给出 allow/ask/deny 用户矩阵，以及容器、网络、profile、UIA、MCP、停止和费用的程序边界 |
| 瘦身可执行 | 已按保留/合并/替换/删除列源码、依赖、替代和删除条件；当前确认零个可立即删除模块 |
| 可分阶段验收 | 240 workspace、241 browser、242 desktop、243 policy/log、244+ 删除；每阶段有 Lan 的真实场景 |
| 不破坏已有能力 | 身份、记忆、聊天、主动活动、预算、暂停、角色隔离和模型切换永久留在 VEIL；执行器不直接访问其数据 |
| 独立判断 | OpenCode 不做安全边界或 VEIL 核心；UFO² 不整套引入；Playwright MCP 不原样全开放 |

## 11. 本工单明确没有完成的事

- 没有实现 `ExecutionRun`、`ExecutionPolicy`、Playwright MCP 或桌面任务执行器。
- 没有启用工作台、配置真实项目、改变模型或费用，也没有删除任何旧工具。
- 没有重跑 237/238 的真实 OpenCode/Windows 测试；本机 PATH 没有 OpenCode。
- 没有证明 Docker 网络已隔离、Linux 主机可用、Claude Code 通道与自主醒来已共享执行入口。
- 文档/PR/CI 通过都不等于 Peter 已获得通用电脑执行能力；每阶段仍以真实 Windows 体验为完成标准。
