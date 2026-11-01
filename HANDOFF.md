## 工单 190 交接（2026-11-02）

- 当前分支 `ticket/190-life-records-center`，本地从 main `d4a868c` 建立；Git CLI 推送因执行环境 DNS 受限，GitHub 连接器可读写仓库。
- PR #240 已开放；生活页和记录工具已实现，Ombre 计划与原长期任务可直接展示。审查修复把原长期任务和短期便签的编辑接回各自数据源，并修复夜间摘要隐私、日期、旧数据覆盖及 CI 临时目录问题；正式运行目录未改。
- 下一步：核对新提交 CI 与审查结果，Windows 实机验收仍需单独确认。详见 `docs/tickets/190-life-records-center.md`。

## 工单 189 交接（2026-11-01）

- 分支 `ticket/189-wire-i-tool`。`OMBRE_TOOLS` 加 `I`（Ombre 原样 schema + 一段英文说明），结果走隐藏记忆过滤；`tools_agent.NATIVE`、`web_agent.memory_names` 同步登记；`memory_agent.call` 的向量预留只在 `content`/`promote`/`supersedes` 时；`_filter_hidden_i` 按条目整块过滤隐藏记忆；`promote`/`supersedes` 指向隐藏记忆直接拒绝；`I` 不放进 `long_tasks` 的副作用/只读名单。一次性通知走现有 `bind_capabilities`。开场不注入 `I`，`feel` 写入没开。
- 未验证：Windows 实际体验、Peter 会不会按「隔天 dream」的节奏用、dream 输出变长后的 token 费用。
- 下一个工单号 190。

## 工单 188 交接（2026-11-01）

- 分支 `ticket/188-read-counts-as-shown`。`NativeMemoryTools._read` 成功后调用 `memory.mark_surfaced([key])`；`_editable` 本来就认 `surfaced_ids`，所以不用别的改动。隐藏/不存在的记忆读失败，不登记；名单上限仍是 `Memory.SURFACED_LIMIT = 40`。连续工具卡片占满屏幕没有处理，另议。
- 下一个工单号 189（187 为 PR #237）。

## 工单 187 交接（2026-11-01）

- 分支 `ticket/187-recall-relevance`。新文件 `recall_relevance.py`（向量解码/余弦/只读读 `embeddings.db`/`QueryEmbedder`/上文拼接）；`Memory.context(query, recent=…)` 在 `[memory.recall_context]` 开启且 `min_similarity` 已填、这句不超过 `short_chars`、有上文时才走 `assisted_recall`；其余路径一字不变。`veil_agent.py` 把 `self.history[-8:]` 传入。`EMBED_API_KEY` 从仓库 `.env` 读，缺失则本轮保持今天的召回。`Memory.__exit__` 关闭 embedder。
- 默认关闭，`character.toml` 只有注释。校准用：开着诊断看 `diagnostics.jsonl` 的 `assisted_recall` 分数；校准前不要写 `min_similarity`。
- 未验证：阈值、延迟、费用（每次启用多一次给「上文+这句」算的查询向量，并多一次 `breath_search` 向量检索，价格 ¥0 配置）、Windows 实际体验。
- 下一个工单号 189。

## 工单 186 交接（2026-11-01）

- 分支 `ticket/186-deepseek-window-64k`。只改 `characters/peter/character.toml`：`[providers.deepseek] max_input_tokens` 32768 → 65536。聊天裁剪用 `cfg.provider['max_input_tokens']`（`veil_agent.py` 约 3116–3282），所以历史会多保留，不再先裁光。同一上限还影响两处：短期便签提取批次上限 `min(MAX_REQUEST_BYTES, max_input_tokens // 2)`（`short_term_notes.py` 约 532，由 16384 字节变为 20000 字节，一次便签整理的输入略多）；每次请求的费用预留 `Budget.reserve` 按上限算输入部分，预留约由 ¥0.07 变为 ¥0.14（只在接近每日上限时才可能影响放行，结算仍按实际用量）。`gpt` 档不变；用户在「模型」页自加的模型仍用自己的上限。
- 未验证：每天多花的费用（估算 ¥1–3，最坏约 ¥9.8）和 Windows 实际体验；按 `docs/tickets/186-deepseek-window-64k.md` 核对 `data\costs.jsonl`。
- 下一个工单号 187。

## 工单 185 交接（2026-11-01）

- 分支 `ticket/185-trace-pin-needs-reason`。`NativeMemoryTools._trace`：新置顶（记忆当前未置顶）时 `edit_reason` 不能为空，否则拒绝，Ombre 不被调用；已置顶的记忆再次 trace 不受影响。trace 工具说明补了这条。测试：`tests/test_memory_importance_browse.py` 加 1 项，旧的上限测试补理由。
- 下一个工单号 186。

# 交接

## 工单 184 交接（2026-11-01）

- 分支 `ticket/184-memory-importance-browse`。`NativeMemoryTools`：`hold` 新增 `importance`/`pinned`（`PINNED_LIMIT = 8`，`_pinned_ids()` 数本地文件里的 `pinned: true`，`trace(pinned=1)` 同样检查）；`memory_catalog` 新增 `order`/`created_from`/`created_to` 和 `total`；`breath_search` 新增 `date_from`/`date_to` 并原样传给 Ombre。`memory_agent` 的 `created_id`/`record_native` 认得 `📌钉选→`。
- 没有用官方 `breath_advanced(catalog=True)`：它没有 ID、没有翻页、超过 50 条静默截断，见工单文档。
- 未验证：Windows 上 Peter 是否会合理使用 importance；置顶后开场记忆是否按预期置顶（Ombre `breath()` 的 pinned 置顶行为只读过代码）；官方日期过滤在真实库里的命中。
- 下一步（等 lan 审核）：110 条记忆的重要度/置顶建议表，只出表，不改数据；之后评估 `hold(feel)` 和 `I`。
- 下一个工单号 185。

## 工单 183 交接（2026-11-01）

- 分支 `ticket/183-memory-update-notes-json`。`Memory.surfaced_ids` + `NativeMemoryTools._editable`：召回或搜索过的记忆可 `hold(target_id)`；`short_term_notes._parse_json_object` 容错并在 error 行记录 `reply_head`/`reply_chars`/`consecutive_failures`。
- 审计报告（项目文件 `audit/memory-system-audit-2026-11-01.md`）里其余建议未做，等 lan 决定：召回带上一轮上下文、瞄一眼跳过召回、整理重复记忆、记忆条目偏长、整理时只看得到最近 48 小时。
- 未验证：Windows 上便签是否恢复整理、Peter 是否改为追加而不是新建。
- 下一个工单号 184。

## 工单 182 交接（2026-10-31）

- 分支 `ticket/182-desktop-bugs-batch`。`DesktopInputMonitor.own_input()` 取代画布/键盘输入前后的 `mark_own_input`；发现应用的安装文件夹 exe 识别（`_vendor_dir_pattern`、`is_blocked_window_exe`）；`desktop_icons` 的 `display_obstructions`；`restore` 处理最大化；搜索改 `auto` 并报真实原因。
- 测试：新增用例通过；全套仅剩与 main 相同的 6 项沙箱失败。
- 未验证：Windows 真机上画图中断是否消失、桌面整理软件进程名、搜索真实失败原因。若画图仍中断，先查 uiautomation `MoveTo` 是否用 SetCursorPos 以外的方式在 `own_input` 之外延迟发输入。
- 小红书图片阅读是新功能，需要 lan 同意后另开单。
- 下一个工单号 183（182 之前预留给 `_tool_chat` 拆分的计划已由 lan 决定不做）。

## 工单 181 交接（2026-10-31）

- 分支 `ticket/181-split-hub`。新增 `hub_screen_pop.py`，`ScreenPopMixin` 放 `request_screen_pop`、`next_screen_pop`、`claim_screen_pop`、`record_screen_pop_event`，方法代码原样搬出，`Hub` 继承它；状态仍在 `Hub.__init__` 创建。
- 测试：全套与修改前失败项一致（沙箱环境限制）。
- 媒体与语音等其它组暂未拆：测试里有对 `web_agent.urlopen` 的替换，直接搬会失效。
- 下一步：182 拆 `_tool_chat` 的工具执行步骤。

## 工单 180 交接（2026-10-31）

- 分支 `ticket/180-split-make-handler`。`web_agent.py` 的 `make_handler` 里每条 GET/POST 路由搬成独立 `_get_*`/`_post_*` 方法，`do_GET` 只做分发，`do_POST` 先处理游戏路由、令牌校验和通用 JSON 解析再分发；只搬代码，唯一控制流改动是 `/api/memory/panel` 的 `if` 改为 `elif`，路由与鉴权顺序不变。
- 测试：`test_web` 等 82 项结果与修改前一致。
- 下一步：181 拆 `Hub` 的独立职责。

## 工单 179 交接（2026-10-31）

- 分支 `ticket/179-unused-imports-and-stale-docs`。只删无用 import 和一个多余的 `nonlocal`，不改行为；有意没动的项见 `docs/tickets/179-unused-imports.md`。
- 测试：全套与修改前失败项一致（沙箱环境限制）。
- 下一步：180 `make_handler` 路由拆分（先选边界清楚的部分）。

## 工单 178 交接（2026-10-31）

- 分支 `ticket/178-dead-code-cleanup`。只删死代码，不改行为；删除项与「有意没删」的项见 `docs/tickets/178-dead-code-cleanup.md`。
- 测试：全套与修改前失败项一致（沙箱环境限制）。
- 下一步：179 无用 import、JS 死代码、重复实现、过期文档。

## 工单 177 交接（2026-10-31）

- 分支 `ticket/177-budget-chat-icons-reliability`。改动：`veil_agent.py`（`Budget.mark_dispatched`、`recover_interrupted`、`settle` 的 `tolerate_over_bound`，聊天请求前写 dispatch 标记）、`autonomy.py`（自主 `run()` 的 finally 里状态恢复放进嵌套 finally）、`web_agent.py` 与 `desktop/chat.js`（单条事件渲染失败不卡轮询；保存失败显示真实原因）、`desktop_icons.py`（快照不得为空，复用校验时读到的布局）、`desktop/chat.html`（价格 step=any）、`veil_agent.py` 的撤回（`check_recallable`、`RecallRejected`；`apply_pending_recall` 遇到已过期的撤回只跳过）。
- 账本新增事件类型 `dispatch`（只有 id）。旧版本读不了含它的账本，所以升级后不要回退。
- 已有的旧未结算预留（没有 dispatch 标记）在下次启动时按「没发出」释放为 0；这是对历史数据的一次性处理，真实花费以供应商账单为准。
- 测试：`tests/test_ticket_177_reliability.py`；4 个旧测试里「重启后仍是待核对」改为「重启后记为已花费」。
- 下一步：合并后验收；后续工单候选见 STATUS「下一步」。

## 工单 176 交接（2026-10-31）

- 分支 `ticket/176-nowhere-travel-display`，基于最新 main `ee83ea1`。界面已在 Chrome 中以现有桌面 CSS 和虚构工具结果预览；Python 工具活动专项 9 项、桌面解析专项 6 项、JavaScript 语法检查通过。桌面全套 Node 检查最近一次为 15 项通过，1 项既有 `backend.test.cjs` 因当前环境回环 HTTP 被拒而无法连接其临时测试服务。PR #226 最新 head 的 Tests（Windows/Ubuntu）、Windows desktop、Dependency security 均通过。Cubic 与 CodeRabbit 有效意见已处理，评审线程均已解决。Electron 依赖目录在该 worktree 不存在，因此未运行 Electron 应用本身；Lan 的 Windows 实机体验尚待验收。
- 实现：成功 `mcp__nowhere__say` 新增保存到既有工具日志的可选输入元数据；`web_agent.py` 仍通过当前角色日志投影它；自主活动详情解析规范 MCP 工具名称（包括角色别名），优先展示返回字段，`quotes` 明确为查询结果并去掉同活动匹配写入语录的所有副本，原始结果可折叠查看且轮询时保持展开状态；失败、截断、格式异常回退既有结果展示。未新增旅行档案或改变 Peter 自主活动行为。
- PR #226 已开放（https://github.com/yecays-ai/veil-agent/pull/226）。Cubic 首轮后已提交审查修复：保留规范工具别名，畸形 quote 结果回退原文，清除与 say 相同的全部 query 副本，展示 bury 来源，轮询保留原始 JSON 展开状态，初始化坏参数路径，并移除本机目录。Cubic 关于 say 元数据“未接线”的意见已附现有成功结果日志调用链和角色投影测试依据。CodeRabbit 追加的 bearer 凭据格式、避免误改普通授权语句、完整处理 `api_key is` 多词值、截断输入不参与去重均已补修并加回归测试。最新 PR head 的 Tests、Windows desktop、Dependency security 均通过；Cubic/CodeRabbit 意见已处理且线程已解决。待合并后由 Lan 按 `STATUS.md` 完成 Windows 实机验收。
- 注意：历史 `say` 日志未保存工具输入，无法从旧 say 回执恢复原文；10 月 31 日的原文仍由同次 `quotes` 查询返回值显示。`bury` 的文字和物品来自其实际回执；地点若只返回坐标，则界面也只展示坐标。没有运行真实 Nowhere MCP 写入操作。

## 工单 175 交接（2026-10-31）

- 独立分支 `ticket/175-image-recovery-and-context-fix`。实现紧急修复：失败图片重启后自动发送 + 上下文长度误拦截 + 重复错误提示：
  1. 消息状态机闭环与终态保证：在 `veil_agent.py` 中为 `incoming-submissions.jsonl` 实现完整状态流转（`received`、`processing`、`committed`、`failed`、`cancelled`、`uncertain`）。`TooLong`、预检不通过或提交异常均写入 `failed` 终态；`finish_media_submission` 未提交时调用 `fail_incoming`；新对话 `/new` 自动将上一会话未提交请求作废为 `cancelled`；服务启动恢复 `recover_uncommitted_incoming` 排除非正常状态，崩溃前中断处于 `processing` 的请求自动记录为 `uncertain` 并保留审计记录，绝不自动投递至 `hub.inbox` 产生旧消息。
  2. 单图上下文多级裁剪与透明诊断：在 `veil_agent.py` 中重构上下文预算与裁剪次序。在发送新图片时，自动将历史记录中的所有旧图坍缩为文本占位符，消除每张 4096 tokens 的旧图虚高；采用“历史聊天轮次 → 长期记忆召回（`recalled`） → 辅助运行上下文（经历/便签/背景/弹幕）”的阶梯裁剪机制，确保核心角色身份（Persona）、必要状态权限与用户当前图片完整保留；若在裁剪所有可裁剪内容后仍超限，返回包含透明 tokens 分项（系统人设、工具定义、用户输入等）的明确诊断，不再模糊报错。
  3. 错误提示去重与分类：在 `web_agent.py` 与 `desktop/chat.js` 中建立请求 ID 与事件 ID 级去重屏障，同一请求或失败事件严格只渲染一次，杜绝重放轮询导致的重复提示，去除“（重复 n 次）”文本追加。
  4. 自动化测试：新建 `tests/test_image_recovery_and_context.py`（8 项用例），覆盖失败图片重启不复活、崩溃中断记录 uncertain 且不自动重发、新会话作废上一会话未提交消息、单图自动裁剪历史与记忆成功入模、真实超限透明诊断、Hub 错误去重、未提交媒体终结 failed 及媒体文件缺失处理。全量 Python 测试（863 项通过、14 项跳过）与桌面测试（10 项通过）全数通过。
  5. 下一步：推送独立 PR，由 Lan 在 Windows 实机按 A–D 步骤完成验收。见 `docs/tickets/175-image-recovery-and-context-fix.md`。

## 工单 174 交接（2026-10-31）

- 独立分支 `ticket/174-desktop-drawing-and-icons-fix`。实现第三阶段：连续自主操作与创作 + Windows 桌面图标整理：
  1. 桌面图标管理与 Windows 11 / COM 修复：新建 `desktop_icons.py`，解决工作线程 COM 初始化问题（`pythoncom.CoInitialize`），直接基于 Shell COM `IFolderView` 原生接口与 `GetCurrentInfo` 精确检测 Windows 11 桌面自动排列与贴齐网格状态；使用 `SelectAndPositionItem` 调整图标视觉位置，并回读物理坐标验证；只改变桌面视觉坐标，绝不调用文件系统移动、重命名、复制或删除真实文件，坐标经过防越界与防重叠安全校验。
  2. 强制预备份与一键恢复：整理操作前强制自动拍摄完整布局快照并存入 `Store`；恢复操作根据快照安全复原匹配图标位置，安全跳过已删除或新增的图标，恢复后重新读取并校验坐标；提供 Web API `/api/desktop-icons/snapshots` 与 `/api/desktop-icons/restore` 供 Lan 随时直接还原桌面，不消耗模型预算与活动额度。
  3. Windows 画图与画布交互能力（`windows_canvas_action`）：为 Peter 补齐真实的鼠标画布交互工具（角色别名 `draw_on_canvas`），支持 `click`、`drag`、`stroke`（单笔画及多笔画序列），以及 `save_to_lab` 直接将当前画布作品保存为 PNG 到 Lab（如 `lab/drawings/sun.png`）；具备客户区严格边界校验（`GetClientRect`）、前台激活线程附加保护与按键防黏连（`ReleaseMouse` 释放保证）。
  4. 主窗口识别优化：优化 `windows_observer.py` 窗口过滤规则，过滤掉 WindowsApps 临时产生的 50x50 代理占位窗口，确保 `windows_launch` 稳定绑定到实际的应用主窗口（如画图的 `无标题 - 画图`）。
  5. 人类优先与打断机制：整理、恢复和画布绘制的每步移动与插值循环中，均严格检查 `should_stop()` 与 `DesktopInputMonitor`，Lan 触碰鼠标或敲击键盘时立即释放鼠标并即刻中止后续操作，保留已拍摄快照。
  6. 连续自主操作与 Lab 创作：在 `autonomy.py` 中更新自主规划提示，支持 Peter 在自主唤醒中根据桌面状态自主规划整理，并连续操作已授权软件完成简单创作并保存到 Lab。
  7. 工具注册与别名：注册 `desktop_icons_read`（只读权限 `desktop_read`，纳入长任务只读工具）、`desktop_icons_arrange`、`desktop_icons_restore` 与 `windows_canvas_action`（权限 `desktop_control`）；在 Peter 的 `character.toml` 中配置别名 `scout_desktop_icons`、`tidy_desktop_icons`、`restore_desktop_layout` 与 `draw_on_canvas`。
  8. 自动化测试：`tests/test_desktop_icons.py`（18 项用例）、`tests/test_desktop_icons_tools.py`（5 项用例）、`tests/test_windows_interaction.py`（42 项用例）全量通过，覆盖 COM 初始化、Win11 定位、快照恢复、画布点击/拖拽/笔画/Lab 保存校验。
  9. 下一步：提交 PR 并关注 GitHub Actions CI 与评审结果；合并后重启 `web_agent.py` 并由 Lan 在 Windows 实机按 A/B 步骤验收。见 `docs/tickets/174-desktop-icons-and-creation.md`。

## 工单 173 交接（2026-10-31）

- 独立分支 `ticket/173-windows-app-discovery`。PR #222 已合并（`b5df397`），核心体验通过，软件实际交互待验收。实现 Peter 自主发现与探索 Windows 软件：
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

- 工单 191（生活页修正）：每日便签只显示 Peter/Lan 主动创建的生活便签，不再混入聊天短期便签（`notes.jsonl`）；「生活」标签可拖出为独立窗口。
- 工单 191：聊天窗口右上角「铺满」按钮改为开关，再点一次恢复到铺满前的小窗（记住铺满前位置大小；Win+↑ 最大化的窗口点一次也会还原）。

- 工单 192：生活页改待办便签（圆圈打勾划掉、`repeat: daily` 每天 0 点复位、已完成一次性便签超过 30 条折叠）+ 日历心情；日记入口移除，旧日记记录保留但不显示；`life_record` 工具新增 `mood`、`repeat`，不能再新建日记；自主唤醒内容改为当天日历标题 + 未完成待办数。
