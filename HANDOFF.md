# 交接

## 工单 173 交接（2026-10-31）

- 独立分支 `ticket/173-windows-app-discovery`。实现 Peter 自主发现与探索 Windows 软件：
  1. 软件发现与安全过滤：新建 `windows_discovery.py`，从当前用户及公共开始菜单（`Programs\*.lnk`）、注册表 `App Paths`（`HKLM` 与 `HKCU`）、WindowsApps 执行别名目录以及常用系统应用中动态发现普通桌面应用；强制排除命令解释器与脚本宿主（`cmd.exe`, `powershell.exe`, `bash.exe`, `wscript.exe`, `wt.exe`, `python.exe` 等）、安装卸载程序（`uninstall`, `unins000`, `setup`, `installer` 等）、系统管理/服务提权工具（`regedit.exe`, `mmc.exe`, `cleanmgr.exe`, `dism.exe` 等）以及敏感凭据保护进程（`PROTECTED_PROCESSES`）；过滤快捷方式中包含重定向、管道、脚本执行的危险注入参数。
  2. 身份绑定与通用启动：生成安全规范的唯一应用 ID（`app_<slug>`，防冲突且不覆盖静态配置），自动绑定可执行路径与进程特征（`process_paths`）；动态注册到 `WindowsInteractionTools`，复用已有 `windows_launch` 启动链路，核对实际进程身份与目标窗口，支持已开启窗口安全复用，超时明确上报 `InteractionUncertain` 并推荐先观察。
  3. 分级权限与强制安全边界：发现（可读取信息）、启动（可启动普通安全应用）、观察（可读取 UIA 控件与截图）、交互（安全操作受控开放）。针对动态发现的应用，在程序层强制拦截：
     - 文件对话框（`#32770`、打开、另存为、浏览、导出等）直接拒绝执行，彻底杜绝遍历或修改用户私人文件；
     - 模糊确认按钮（上下文不明确的“确定”、“OK”、“Yes”、“是”、“Apply”、“应用”、“Continue”、“继续”）在未经验证的对话框或模态窗口中直接拒绝执行；
     - 文本输入控件严格过滤，禁止向路径、命令行或地址栏输入文本；
     - 沿用 `_BLOCKED_CONTROL` 拦截删除、卸载、安装、提权操作。
  4. 人类优先与打断机制：沿用 `DesktopInputMonitor`、`desktop_access` 互斥锁与 `should_stop` 回调，Lan 移动鼠标或敲击键盘时立即中断桌面动作。
  5. 工具注册与角色别名：在 `tools_agent.py` 与 `windows_interaction.py` 中注册 `windows_discover_apps`（`tool_policy` 为 `desktop_read`，在 `long_tasks.py` 中加入 `READ_ONLY_TOOLS`），在 Peter 的 `character.toml` 中配置别名 `windows_discover_apps = "scout_apps"`。
  6. 自动化测试：`tests/test_windows_discovery.py`（7 项用例）覆盖快捷方式解析、安全过滤、危险参数拦截、唯一标识生成、文件对话框/模糊确认按钮/危险文本拦截判定及真实 Windows 发现；`tests/test_windows_interaction.py`（38 项用例）全面覆盖工具发现、过滤查询、动态启动与窗口复用、未授权启动拦截、文件对话框拦截、模糊确认拦截、危险文本输入拦截及安全控件操作。全量测试均通过。
  7. 下一步：提交 PR 并关注 GitHub Actions CI 与 Cubic 评审意见；合并后由 Lan 在 Windows 实机按 1–3 步验收。见 `docs/tickets/173-windows-app-discovery.md`。

## 工单 167 交接（2026-10-30）

- 独立分支 `ticket/167-lab-python-sandbox`。实现 Peter Lab 独立代码实验室：
  1. 隔离执行与资源限制：基于本地 Docker Desktop 容器（`python:3.12-slim`），严格断网（`--network none`），限制 CPU 1.0、内存 256MB、无额外 Swap（Linux/WSL2 下 `--memory-swap 256m`）、进程数 64，根文件系统只读（`--read-only`），`/tmp` 内存受限（16MB），不继承宿主机环境变量，一次性临时工作区绑定挂载，不挂载任何宿主机个人/系统/代码目录；实时监控 50MB 磁盘配额防止宿主机磁盘被填满。
  2. 管道防死锁与非可信包装：通过独立后台 reader 线程流式清空 stdout/stderr 管道，彻底杜绝大量输出导致的 OS 管道死锁；执行结果通过 `<untrusted_lab_python>` 标签包装，防范提示词注入。
  3. 失败阻断与默认关闭：Docker 未安装或未启动时，`check_docker_available` 快速拒绝并返回友好中文提示，坚决不退回宿主 Python 进程执行；沙盒功能默认关闭，仅 Peter 配置中显式启用。
  4. 成果写回与防损坏：执行前记录项目文件 SHA-256 哈希；超时、打断或磁盘超限跳过回写，防止持久化损坏的截断文件；正常运行后通过原子替换写回 Peter 的 `lab/` 目录。
  5. 自主调试闭环：执行输出包括 stdout、stderr、执行耗时与状态。脚本语法错误或运行时异常（如除以零）时返回完整 traceback，工具状态保持成功，使 Peter 能在模型上下文中观察错误并自主修正重试。
  6. 打断与超时：支持默认 15 秒（最大 30 秒）硬超时与 `should_stop` 用户打断，超期自动执行 `docker kill` / `docker rm -f` 强制销毁容器；已将 `run_lab_python` 纳入 `veil_agent.py` 的可打断工具调度。
  7. 长任务与配置集成：将 `run_lab_python` 纳入 `long_tasks.py` 的 `SIDE_EFFECT_TOOLS`，在 `veil_agent.py` 的 `load_character` 中保留 `python_sandbox` 配置校验。
  8. 角色别名：在 Peter 的 `character.toml` 中配置别名 `run_lab_python = "tinker_in_lab"`，并显式配置 `python_sandbox = true`。
  9. 自动化测试：`tests/test_lab_sandbox.py` 覆盖路径安全、Docker 探测与隔离参数断言、管道防堵塞、磁盘配额超限防损坏、超时/打断清理、原子成果回写、真实 Docker 执行及断网验证，21 项测试全部通过；相关 `test_tools`、`test_lab`、`test_lab_preview`、`test_chat` 回归全部通过。
  10. Windows 实际体验按 A–E 待 Lan 验收。见 `docs/tickets/167-lab-python-sandbox.md`。

## 工单 172 交接（2026-10-30）

- 基于工单 171 合并提交 `1d4f91e` 建立 `ticket/172-preserve-tool-results`。`model_tool_result()` 不再按中文字符或工具白名单丢弃成功结果；明确的程序成功提示在 `tools_agent.py` 生成处改为英文。
- 新增中文桌面、记忆、混合语言、拒绝与失败结果回归，并将现有长结果测试改为中文正文；MCP、浏览器、Windows 桌面的不可信包裹沿用原逻辑。
- PR #220 已合并。

## 工单 168 交接（2026-10-30）

- 分支 `ticket/168-game-results-desktop-images` 使用独立 worktree。原定 167 与本机 `ticket/167-lab-python-sandbox` 冲突，Lan 明确指定本单改 168。正式目录保持 main；其中原有未跟踪文件未触碰。
- 已实现游戏本地入口、受限成绩追加/去重、页面保存提示，以及 Desktop 图片读取到既有视觉消息链路。允许本机回环连接后，HTTP 实连用例通过；完整 Python 测试 785 项中 774 项通过、11 项跳过。没有 Windows 实机体验。
- 下一步：检查 PR #216 当前 head 的 CI 和 Cubic 实质审查意见，修复功能、数据或安全问题后按仓库规则合并；Windows 体验请 Lan 完成。GitHub 域名解析失败导致本机 pull/push 不通，GitHub 连接已上传与本地 tree 一致的远端提交。PR #215 已合并，合并提交 `5d240da` 与本地 main 相同。

## 当前进度

- 工单 170：只改 `update.bat` 一行路径和 `tests/test_update_bat.py` 一条断言；update.bat 运行副本里的 `%~dp0` 是 `.local-tmp`。下一个工单号 171。
- 工单 169：分支 `ticket/169-glance-continuity`，基于 GitHub main `5d240da`。`recent_experiences.py` 复用 Store 保存最近一次瞄一眼的时间、应用/标题、模型摘要和该次最多四条显示得到确认的弹幕原话；`Chat` 每轮注入，下一次瞄一眼启动时立即替换，跨轮/跨重启不消费、不设时限。记录只供按需接话，不引导主动重复。正文位于角色私有经历文件，不进工具活动账本，原始 UIA 不落盘。含五项链路用例的 121 项专项、聊天、自主与缓存回归通过；网页 HTTP 回归受本机回环网络权限限制，待 CI 验证；Windows 正式模型体验待 Lan 验收，步骤见 `docs/tickets/169-glance-continuity.md`。下一工单号 170。
- 工单 169 的下一步：完成本地回归、提交 PR，按最新 head 核对 CI 和实质评审；合并后请 Lan 验收弹幕后接话、连续追问与下一次替换。终端 Git 无法解析 GitHub，因此源码快照从 GitHub API 按固定 SHA 拉取，未改正式运行目录。
- 工单 166：PR #214 已合并为 `3d0a5be`；Lan 实机截图定位到上游 `open_door` 的旁观者提示语尾逗号会构造 tuple，阻断落地。当前安装源码已本地修复；PR #215 的安装器包含幂等兼容修正，并以 Windows PowerShell 5.1 安全的 ASCII 码点准确匹配含 `{_web_url}` 的上游语句。定向 AST、安装器解析和精确匹配检查通过；修正提交后的最终 head CI 待完成。服务重启后真实工具返回、旅程恢复等场景仍待 Lan 验收。
- 工单 165：PR #213 已合并为 `589bb17`；Windows 实际体验待验收。
- 下一步：等待 PR #215 最新 head 的自动检查和实质评审意见；若无阻断则按用户授权合并。Windows 服务重启后工具真实返回、旅程恢复、Lab 文件、自主停止/暂停与地图访问仍待 Lan 验收。
- 工单 164：PR #212 仍开放；Windows 实际体验待验收。
- 工单 163：PR #211 已合并，必需 CI 已通过；Windows 体验验收待按 `docs/tickets/163-mcp-integrations.md` 执行。
- 工单 162：PR #210 已合并；在 main 手动运行 `Build Xiaohongshu Windows sidecar` 后，Lan 更新并按 `docs/tickets/162-xiaohongshu-page-wait.md` 验收。

- 工单 160（PR #208）：上游 v2.5.5 浏览器页面失败返回 HTTP 500 + `STATUS_CHECK_FAILED`/`LIST_FEEDS_FAILED`/`SEARCH_FEEDS_FAILED`/`GET_FEED_DETAIL_FAILED`（未登录是 200 + `is_logged_in:false`，因 pkg/errors `Wrap(nil)` 返回 nil）。这些改为 `PageFailure(Transient)`；只有 `PageFailure`（含 HTTP 超时、整次超时）计入 `transient_streak`，连续 3 次锁死并说明，成功清零；其他拒绝文字带 `HTTP <状态> <代码>`（代码仅 `[A-Z_]`）。`xiaohongshu_setup.login_and_bind` 复查遇 `PageFailure` 时，绑定文件有效则提示直接启动 VEIL。下一工单号 161。
- 工单 159：PR #207 已合并 `f302ac8`。短期便签分批提取、失败诊断及预算结清已实现；聊天缓存用量新增来源标记，旧版 0 保守显示为未核实。Claude 评审补了：单轮超长对话按字节截断后照样推进检查点；`settle` 报用量异常时按预留结清并继续使用输出（不再每 30 分钟重发重扣）；`prompt_tokens==0` 的占位结算不计入缓存统计。Windows 体验待验收。
- 工单 158：`xiaohongshu_tools.py` 新增 `Transient`（HTTP 超时/连接错误、伴随程序启动超时、账号锁占用）与 `OPERATION_TIMEOUT=120`（单次 HTTP `TIMEOUT=60`）。临时失败不设 `blocked`；整次超时停止本次读取但不锁死；`should_stop` 仍锁死（原行为）。掉登录/账号不符给专门文字，其余拒绝仍锁死；锁死后再调用提示「still paused」。每次失败写私有目录 `data/<id>/xiaohongshu/errors.jsonl`；过期 note_ref 只拒绝不锁死；锁死检查在账号锁之前；超时后 worker 2 秒内不退出则锁死并如实说明（kind/error/detail/seconds，detail 只含本程序 ValueError 文字）。PR #206 已合并 `7b91d83`。
- 工单 157：分支 `ticket/157-raw-tool-format-guard`，PR #205 已合并（`0b6c005`；Claude 补了拦截按失败处理、中文紧邻工具名、任意首参数、逐个标记查找）。修复两种模型/接口可能把内部 `send_message` 调用格式当成普通回复返回的情况：参数文本及 `[Message 1]` 批次文本现在被识别、拦截，不解析、不执行；诊断只记录格式类型、模型、请求步数和正文长度，不写泄漏正文。新增覆盖两种复现格式、普通提及不误拦的回归测试。等待 Lan Windows 验收。
- 工单 156：PR #204 已由 `yecays-ai` 合并，合并提交 `7a3e6b18289b601af12ea9cd06f287bf221424f4`。代码/CI 与 Windows 发行文件检查已记录；真实账号体验仍待 Lan 验收。此前状态文本过期，已按 GitHub 当前状态更正。
- 156 证据：18 项专项通过（含自主醒来搜索→本地草稿→拒绝发布），评审修复后全仓库复查 730 项无失败、11 项跳过；最后增补与评审修复后 18 项专项通过，含真实 Windows 父子进程退出。91cefb4 的四平台回归和安全检查通过。CodeRabbit 首轮审查完成，2 条实质问题均已修复：登录程序用 Popen + finally 整树清理，阅读服务在账号锁内 finally 关闭；进程启动/关闭另有线程锁。复核及最终 head CI 以 PR #204 为准。另修角色小返回额度严格生效，人工 RELOGIN 保留绑定和配额。真实 Windows 发行文件校验/启动参数及服务健康 200、未鉴权 401 通过，测试进程已退出。没有登录用户账号，真实搜索/正文/评论与草稿体验不能算通过。研究、平台限制和 Windows 三步体验验收见 `docs/tickets/156-xiaohongshu.md`。真实专属账号体验仍待 Lan 验收。
- 155 的 PR #203 已合并到当前 main；实现 head 的跨平台 Python、审计等检查通过。其浏览器交接修复已进入 main，视频播放与 Lan Windows 整段体验仍待验收。155 实现证据见下一条。
- 工单 155 实现证据：独立 Playwright 浏览器只允许公开 HTTPS 只读导航；已修复正常/异常/取消后停止闭包释放。PR #203 已合并，10 项专项及实现 head CI 通过；真实 B 站页面可打开，视频播放未确认，Windows 整段体验待验收。
- 工单 154 已由 PR #202 合并为 `42f277c11cf1fc0903fdb5322aaa663e0c43e9f8`；合并提交四项跨平台检查通过，匿名源码读取和受限官方只读 MCP 实现已进入 main；真实官方鉴权与 Lan Windows 体验待验收。
- 工单 153 已由 PR #201 合并为 fef11147ce2a7ecc9030773d419f5d81b013ef00；该 main 提交四项跨平台 Python 检查通过，Windows 体验待验收。

- 工单 153：聊天轮里模型返回空正文且没有工具调用、Peter 也没调用 `stay_silent`/`reply_later`、本轮没发过消息时，视为接口故障：写 `data/<id>/memory/_agent/diagnostics.jsonl`（code `empty_reply`，finish_reason、tokens、reasoning 长度），用同一组消息重问一次（约 ¥0.015/次）；仍空则抛 `RequestFailed`（处理状态 `failed`，Lan 看到提示）。沉默只来自 `stay_silent`。起因：抗截断 gemini 通道 10-28 晚 27 轮里 7 轮空回复被当成不回。体验待验收。
- 工单 152：`long_tasks.finish_tool` 工具返回成功即清除 pending（只有出错/不确定/中断才需读回核对）；新模块 `thoughts.py`（`ThoughtIndex`，工具 `thought`，`data/<id>/thoughts.json`，passing 3 天无触碰消失、touch 3 次转 kept、done/dropped 30 天清除、上限 40 条活跃；`wake_text()` 最多 2 条、24 小时不重复）；`autonomy._lab_context` 改为最近 12 个文件列表 + 最新文件前 600 字节；`autonomy.jsonl` 新增 `model`；通知 `wake_freedom_152_announced`（`web_agent` 启动时与 150 通知一起排队）。下一个工单号 153。
- 工单 151：分支 `ticket/151-windows-permissions`，PR #198 已合并为 25acf2b，目标 `main` 基于 `e23a373`。新增 `DesktopFileReadTools`（独立只读目录权限）、Chrome Guest 公开 URL 新窗口（复用现有 Windows 观察）和 `WindowsMediaTools`（GSMTC）；桌面读取与媒体读取分别配置开关，Chrome 控件及窗口管理动作不在白名单。Chrome 使用 `--guest --incognito`、每次独立的临时用户数据目录（进程退出清理），不读 Lan 的 Chrome 资料或 Cookie，不复用/激活 Lan 现有窗口；必须检测到新窗口才报告成功，未安装、进程启动失败和窗口未出现均明确报错。针对性回归共 58 项，56 项通过、2 项因环境不能创建链接而跳过；`py_compile` 与 `diff --check` 通过。首次 GitHub API 上传审查因认为含历史会话链接的状态/交接记录未获明确外发授权而拒绝；Lan 随后明确授权保留记录并创建 PR，正常 Git 推送已成功。Windows 体验待验收，PR 已合并。
- 工单 150 已由 PR #197 合并（`e23a373`）；该提交的 5 项 GitHub 检查全部通过，Lan Windows 体验待验收。
- 工单 149：分支 `ticket/149-peter-lab-preview`。已实现隔离 Chromium Lab HTML 预览、截图当轮输入、错误诊断、超时/迭代限制、回归测试、安装脚本与 Windows CI；新增工单文档并更新 STATUS。本机提交 `b483850`；因本机 DNS 无法访问 GitHub，使用仓库 Git API 发布同一内容，PR #196 已开放。已运行记录中的针对性测试与 diff 检查。CI 的 Python 3.11/pyee 14 不兼容、测试替身没有初始化 lab_preview、依赖测试未忽略空行已修正，待发布修复提交并核对新 CI；CodeRabbit 已请求，Cubic 试用额度用尽。保持 PR 未合并。真实 Chromium 集成只在 Windows Python 3.12 CI 中运行；Lan Windows 体验待验收。
- 工单 148：只改记录。STATUS.md 改为「验收总表（按功能）」+「待验收与待确认」+「工单记录（按编号）」三块；按 Lan 2026-10-28 确认把 097、099–101、106–112、115–118、123、127、128、131、133、134、137、139、141 记为 Windows 实际体验验收通过；098 等 2026-11-07；142–147 按真实记录待验收；135/136/138 待她确认。下一个工单号 150。
- 工单 147：`memory_native.neutral_dream_header` + `DREAM_NOTE`（只换 `=== Dreaming` 标题与第一个空行之间、含 `trace(` 且不含 `ID:` 的引导段）；`veil_agent` 通用工具分支 `json.loads(arguments or '{}')`，`null` → `{}`。下一个工单号 148。
- 工单 146：核心可靠性修复，分支 `ticket/146-core-reliability`。已完成：1. 解耦 `persist_incoming` 准入写入与 `chat.jsonl` 正式历史，引入 `incoming-submissions.jsonl`，消息在实际轮次开始或插话时提交至 `chat.jsonl`，修复连发/插话时的历史错序、prompt 用户消息重复注入及问答归类混乱，服务重启时自动补录排队中未开始的消息，失败媒体上传清理 submission 记录以允许重试；2. 修复短期便签 `_call_extractor` 异常时费用预留泄漏并在 `Budget.__init__` 初始化时对账清理，防止 pending 累积阻断聊天；3. 引入专用 `EmptyReplyError(UserError)`，`run_chat` 文本分支针对性捕获模型空回复，防止后台服务异常崩溃退出的同时保留存储与账本致命异常的向外暴露保护。CodeRabbit 3 项评审意见已全数修复。新增/更新回归测试全部通过。下一个工单号 147。
- 工单 145：`memory_native.OMBRE_DESCRIPTIONS` + `ombre_tool_for_model`（替代 trace_tool_for_model；去 schema title、去 trace 被拒参数）；`tools_agent.FILESYSTEM_DESCRIPTIONS`（permitted_tools 里替换说明、去 `$schema`）；long_tasks、windows_observer、windows_interaction 说明缩短；veil_agent 输出预算后缀缩短。下一个工单号 146。
- 工单 144：消息队列原子性与回复状态可靠性修复，分支 `ticket/144-chat-queue-reliability`。已完成：1. `reply_later(cancel=True)` 重置 `reply_later_called` 与 `turn_done`，同时增加 `message_turn_done` 防止误清除 `send_message(done=True)` 的轮次结束标记；2. `_chat_batch` 与 `claim_steer` 实现原子出队，避免 `ChatAction`（如 Request Reply）被当成文本吞掉；3. 语音消息批次重入队改为持锁逆序 `appendleft`，严格保持真实消息时序 A → B → C。新增/完善 5 项回归测试全部通过。下一个工单号 145。
- 工单 143：`_record_tool_result` 对成功的 stay_silent / reply_later 不输出聊天行；`run_chat` 不再输出 `trim_note`（仍计算，见上下文页）。下一个工单号 144。
- 工单 142：插话重置 send_message 10 条额度（`accept_steer`），第 10 条后 `turn_done`；请求回复按钮改图标（`desktop/icons.js` 加 sparkles）；新工具 `stay_silent`（`silent_chosen` 与 reply_later 同路径静默结束），通知键 `stay_silent_142_announced`。下一个工单号 143。
- 工单 141：PR #188，分支 `ticket/141-free-reply`。自由对话核心、桌面输入状态/Request Reply、reply_later 持久化与限额已实现；CodeRabbit 五条评论已一次修复，本地全量 629 项通过、7 项跳过，桌面 10 项通过。Claude 接手复审：补 `reply_later` 只传 `cancel: true` 可作废自己挂起的晚点回（不结束本轮），能力通知同步说明，加 1 项回归。下一个工单号 142。Lan 2026-10-28 确认验收通过。
- 工单 140：长期任务进度满 40 条滚动归档（`long-task-progress-archive.jsonl`），同一步先读后确认可通过。下一个工单号 141。
- 工单 139：Peter 自己调醒来节律（`set_wake_rhythm`）、多个带留言的闹钟（最早 15 分钟）、自然醒随机（`autonomy._natural_wait`，每轮门槛由时间种子决定）。闹钟文件改成 `alarms` 列表，旧格式兼容。没改 `desktop/`。下一个工单号 140。
- 工单 138：send_message 加 `done`，聊天加「停下」按钮。起因是 argon 不会空着回复收尾，一直发到 20 步。改了 `desktop/`，Lan 需要 update.bat。下一个工单号 139。
- 工单 137「桌面 UI 提示与连续重新生成」：分支 `ticket/137-regenerate-ui-notifications`。范围为连续重新生成保留原用户消息关联、隐藏桌宠四角调整点、聊天窗铺满工作区、语音识别错误三秒后收起、录音提供删除取消且普通工具通知留在聊天记录。Python/JavaScript 语法检查和 `git diff --check` 通过；PR #184；Claude 按审查意见修正：记录的原消息已删除、或是 Peter 主动发的消息时拒绝重新生成，不再借用更早一轮；新增 `tests/test_regeneration.py`（3 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试 10 项通过。代码和验收步骤见 `docs/tickets/137-regenerate-ui-notifications.md`。Lan 2026-10-28 确认验收通过。
- 工单 136：模型请求等待上限 60→180 秒。Lan 试中转站：`[k]claude-sonnet-5.5` 会把工具回执变成用户「-」（推断），已弃用；现在用 `[vertex-t3按量]gemini-4-argon`（按 token，约 ¥0.01/次），慢且一步发一个气泡。下一个工单号 137。
- 工单 135：请求失败时显示原因，接口原文存 `data/peter/provider-errors.jsonl`。134 已合并（c87d312），Lan 首次用 jiushi Sonnet 5.5 请求失败，原因待这个文件。下一个工单号 136。
- 工单 134：设置里加模型（接口、模型、Peter/记忆后台分开选、顶栏切换、页面可设费用上限）。改了 `desktop/`，Lan 需 update.bat 重新打包 exe。中转站 Sonnet 5.5 按次 ¥0.06；Peter 每天约 80–320 次请求，约 ¥5–20/天。下一个工单号 135。
- 工单 133：PR #180，六部分已实现（见 STATUS.md）。改了 `desktop/`，Lan 需 update.bat 重新打包 exe。下一个工单号 134。
- 工单 132：看得懂的记忆页（列表、分组、编辑、删除）。改了 `desktop/main.cjs`，Lan 需 update.bat 重新打包 exe。下一个工单号 133。
- 工单 131：PR #178，已修复 CodeRabbit 指出的发送期间修改草稿会丢失引用问题；虚构数据桌面测试 10 项和 Windows Electron smoke 通过。Lan 已授权检查通过后合并。Lan 2026-10-28 确认验收通过。

- 工单 130：Codex 提醒不再召回长期记忆（`codex_watch_mode`）。下一个工单号 131。
- 工单 129：能力记录给 Peter 看时转成本地时间，保存仍是 UTC。记忆相关工单今后由 Claude 负责。下一个工单号 130。
- 工单 128：PR #175 已合并（dd4e2c3），当前提交 Tests 与 Dependency security 通过。Lan 2026-10-28 确认验收通过。
- 工单 127：PR #174 已合并（c042ed7），当前提交 Tests 与 Dependency security 通过。Lan 2026-10-28 确认验收通过。

- 工单 126「发完消息不再重复说一遍」：分支 `ticket/126-no-repeat-after-send`，Claude 实现。122 之后 Peter 用 send_message 发完几条，常再写一段换个说法的总结，按 122 的规则会多显示/多发一条（Lan 在 QQ 上看到每段话后面都重复一遍）。现在：发完消息后紧接着写的文字不显示、不发送、不存（以最后一条已发消息为回复）；如果发消息之后他又用了别的工具（如先说「先看一下」再读文件），之后写的文字照常作为新回复。send_message 说明同步改为「紧接着写的文字不会显示」。验收：update.bat 后在 QQ 和桌面各聊 5 分钟，不再出现一组消息后跟一条重述。下一个工单号 127。
- 工单 125「实时上下文窗口」：分支 `ticket/125-live-context`，Claude 实现。新增「上下文」页（可像其他页一样拖出成独立小窗，透明度单独可调）：上半块「聊天时活动」显示最近 10 次请求各带了多少原文轮数、背景段、长期记忆、短期便签、估算 tokens 和这次用了哪些工具（来自 `context-log.jsonl` 与 `tools.jsonl`，按 request_id 对上）；下半块「短期便签」显示进行中的便签（含约何时淡出）和 3 小时内已完成/已过期的，以及下次整理时间。数据随 `/api/events` 下发，只读，不触发提取、不改便签。需要运行 update.bat 重新打包 exe。验收：聊一句后上半块出现这次请求；有便签后下半块出现；拖出「上下文」标签成为小窗，合并回去正常。下一个工单号 126。
- 工单 124「瞄一眼和 Codex 监视能单独暂停」：分支 `ticket/124-toggle-allowlist`，Claude 实现。桌面程序 `desktop/main.cjs` 的请求白名单漏了 `/api/glance` 和 `/api/codex-watch`，两个开关点了都显示「设置保存失败」（Lan 电脑上从未生成这两个设置文件）。现在主窗口和弹出的活动小窗都允许这两个请求。需要运行 update.bat 重新打包 exe。验收：单独点「暂停瞄一眼」「暂停监视」都显示已暂停、自主活动仍开着，重启后保持暂停。下一个工单号 125。
- 当前工单 123「Peter 聊天短期便签（程序提取）」：分支 `ticket/123-short-term-notes`。实现了 Peter 专用、每 30 分钟/新对话前提取、JSONL 追加状态记录、60 分钟淡出、最多 10 条 / 800 字符上下文注入及独立 `short_term_notes` 费用类别；程序不写 Ombre 或 Plan。本地完整测试 560 项通过、5 项跳过。PR #170 已合并（ce414a2）。Lan 2026-10-28 确认验收通过。下一个工单号 124。
- 工单 121「Peter 背景库按关键词注入」，分支 `ticket/121-background-injection`：`background.py` + `Chat.send` 在历史之后、召回之前插入命中的背景段落；本机背景文件 `persona/peter.background.md`（不进仓库）每次请求重读；`data/<角色>/context-log.jsonl` 记录每次请求的各部分大小与背景命中方式。
- 工单 120「瞄一眼认不出前台窗口时不再失败」，分支 `ticket/120-glance-no-window`：前台只有桌面/VEIL/受保护窗口时，瞄一眼不再记失败，Peter 得知 Lan 多半在桌面，并看到桌面项目名字（不读内容）；窗口内容读不出时只给应用名和标题。回归测试 `tests/test_glance.py`。
- 工单 117（PR #165）已合并 `d42b12e`；Tests、Dependency security、Windows desktop 检查通过，工单 119（PR #166）已合并 `5262318`，修复 Codex 监视的能力通知。117 的审批/提问等待仍没有可靠信号。Lan 2026-10-28 确认验收通过。
- 工单 116 的 PR #164 已合并为 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`；GitHub 查询未返回该提交的 workflow/status 行，不能写成 CI 通过。Lan 2026-10-28 确认验收通过。
- 观察路径复用 `IdleClock`、`windows_foreground` 和 `windows_observe` 的 UIA 文字/标题；不传截图给模型。曾有截图进入模型请求的修改被自动审批拒绝，因此当前实现保持 UIA-only，不要尝试绕过拒绝重新传截图。
- 目标行为：最近 5 分钟有输入且屏幕解锁时，每次随机等 15–40 分钟；离开或 `/pause` 时停止计时，回来后重新计时。每次最多 4 个工具动作，每日独立 ¥1 上限。结果进入短期活动记录并在下一次普通聊天交付，不自动保存长期记忆。
- 工单 115（PR #163）已合并 `4a5ed56`，Tests、Dependency security、Windows desktop 检查通过；Lan 本地视觉预览已确认。Lan 2026-10-28 确认验收通过。
- 工单 118（PR #162）已合并 `ffdd368`；针对性检查通过、3 项跳过。Lan 2026-10-28 确认验收通过。

## 下一步

- 验收状态以 STATUS.md「验收总表」为准，后续开发路线以它为依据。
- 098 QQ 邮箱：2026-11-07 后验收收发。
- 142–151：Lan 按各自工单做 Windows 体验验收。
- 135、136、138：请 Lan 确认（10-27 说过 134–138 验收完毕，但不在 10-28 清单里）。
- 下一个工单号为 172；工单 171 对应 PR #219。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 工单 166 的开门兼容修复只在独立 worktree 操作；PR #214 主功能已合并，PR #215 待最终 CI 后合并。Windows 实机验收前保持修复 worktree，暂不清理。
- 不增加第二个 Agent、模型供应商、Agent 框架或依赖；浏览器复用仓库已有 Playwright 依赖；工具别名复用现有角色配置、Chat 工具循环及权限/预算/检查点入口。工单 164 移除了工单 151 的 GSMTC 媒体读取及其 PyWinRT 依赖，其他 Windows 观察能力保留。
- UIA 窗口内容是非可信数据；敏感窗口过滤由现有 Windows 观察工具执行。当前瞄一眼观察仅用 UIA，不调用截图回退，不把原始 UIA 内容写入活动文件或长期记忆。
- 代码检查、CI、Windows 实机体验是不同证据；各工单的体验验收状态以 STATUS.md「验收总表」为准。
## 工单 171 交接（2026-10-30）

独立工作树基于远端 `main` 的 `57aacf0`，PR #219 已开放。首次 CI 已运行，因缺少常量导入与 `pypdf` 旧版漏洞失败；Cubic 的功能、安全和日志意见已在一个后续提交中修复。本地定向回归通过；活动日志只写工具、路径、结果，不记文档正文，写入目录没有扩大。下一步：核对最新 head 的 CI 与实质审查，按仓库规则处理 PR；Windows 体验待 Lan 验收。

