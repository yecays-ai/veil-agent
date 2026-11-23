# HANDOFF

## 工单 429 交接（VEIL Brain P0-B 安全影子同步；2026-11-23）

- 工单：`docs/tickets/429-veil-brain-p0b.md`；分支 `ticket/429-veil-brain-p0b`，从 `main` `d9406e7` 开，
  worktree `worktrees/429-veil-brain-p0b`。**未合并、未在正式库启用**；Lan 审核后再合并。
- 做了什么：新增 `brain_sync.py` 与 18 条 unittest；`memory_agent.Memory` 在确认写入后只入队，
  worker 独立线程、异常全部落到 `brain-sync-log.jsonl`；周期对账按内容哈希/状态/版本/向量/事件逐项比较，
  报告写 `data/<role>/brain-sync-report.md`。记录按 id/source_index/行号/event_id 幂等，软删除只标 removed_at；确认擦除时清内容只留空墓碑。
- 关键行为：硬删除会清掉影子库里的正文、版本与向量，只留空墓碑；扫描不完整时不做删除推断；版本账本被擦除后整体重建；meaning 向量与事件最新修订都参与比较。
- 已核实：P0-A 的 7 个悬空事件来源 = #426 删除的 7 条 feel（备份有对应文件、正式库无相近 id）；
  8 条 sidecar 链接也指向这 7 个 id，源记忆健在。对账从 #426 日志读取 known_removed 并单独分类。
- 已验证：18 条用例（新增/修改+隐藏/删除/归档+合并+版本+向量/关闭时不写+关时不 reconcile/异常隔离/漏同步/源文件不被改/已知引用/db 可校验/队列满/缺库静默/硬删除擦除/账本重写重建/meaning 向量/事件修订/不可读文件不误删）。
- 合并后第一步：`python brain_sync.py init --source <隔离副本>` 建 `data/peter/brain.db`，重启 VEIL，
  确认 `shadow status` 正常；然后开始 7 天观察，期间不改 schema 语义、不启动 P1。
- 边界：影子库只读源、不反向写、不自动修复；故障演练只在隔离副本做。

## 工单 428 交接（VEIL Brain P0-A 隔离导入；2026-11-23）

- 工单：`docs/tickets/428-veil-brain-p0a.md`；分支 `ticket/428-veil-brain-p0a`，从 `main` `54eb4df` 开，
  worktree `worktrees/428-veil-brain-p0a`。正式运行目录仍是 main，P0-A 没有改正式库、没有挂写入链。
- 做了什么：新增 `veil_brain.py`（import/check/inspect）与 6 条 unittest。隔离副本 → 单文件 `brain.db`
  + Markdown 快照 + 对账报告。表覆盖 memories/versions/sidecar_events/sidecar_state/relations/sources/
  quotes/vectors/events/event_sources，另留空表 `event_stages` 给以后用。
- 本机实测（隔离目录 `.local-tmp/brain-p0a/`，已在 .gitignore）：283 条记忆、281 条版本（旧正文与哈希全存）、
  168 条 sidecar 事件（折叠出 109 条状态）、244 条 1024 维向量（原始字节 + SHA）、69 篇事件映射
  （157 个来源，7 个是被 #426 删掉的 feel，记为悬空）；内容摘要 `676f47fa…`。
- 已验证：重新导入摘要一致；`brain.db` 拷到另一个目录仍能 check/inspect；正式库导入前后哈希清单一致
  （310 个文件，`7418ef30…`）。
- 下一个窗口第一步：等 Lan 按工单验收（快照可读、三类抽查、重导、跨目录打开、正式库校验和）。
  验收通过后再开 P0-B（安全影子同步）；P0-B 之前不要改 `veil_brain.py` 的 schema 语义。
- 已知边界：事件正文没有进库；7 个已删除 feel 来源是悬空映射；`memory/_agent/` 与 `.locks/` 属于运行时
  私有目录，已在 `manifest()` 里排除，不参与源清单校验。

## 工单 425 交接（经历页两个 GET 路由没接上；2026-11-23 第五步冒烟发现）

- 工单：`docs/tickets/425-event-timeline-endpoints.md`；分支 `ticket/425-event-timeline-endpoints`，
  从 `main` `d8eacc2` 开（#424 合并提交），worktree `worktrees/425-event-timeline-endpoints`。
- 根因：`_get_memory()` 从 #407 起就处理 `/api/memory/events`（列表）和 `/api/memory/event`（详情），
  但 `do_GET` 只把 `/api/memory/catalog`、`/api/memory/read` 转给它；事件层默认关闭时没人打开过这个
  页面，第五步迁完 69 篇后点开「记忆 · 经历」才发现一直 404。
- 改法：`web_agent.py` 的 GET 分支把这两个路径一起转给 `_get_memory`；`tests/test_web.py` 新增一条
  路由、鉴权与 404 回归。返回结构、页面、事件数据都不动。
- 验证：`python -m unittest tests.test_web` 73 条通过（新增 1 条）。
- 第五步状态（在同一台机器上已发生）：正式库已迁入 69 篇（`tmp/step5/final-prepared.json`），独立索引
  matched 69 / missing 0 / stale 0 / ready；备份 `tmp/step5/backup-preapply-*`（3853 文件，恢复演练
  哈希一致）。正式迁移由 #424 放行后执行，本 PR 只补页面入口。
- 下一个窗口第一步：合并后 `update-main.cmd`、重启 VEIL，Lan 打开「记忆 · 经历」验收列表与详情；
  然后按第五步报告继续自然聊天观察项。

## 工单 424 交接（导入事件与旧碎片只算一次回忆；2026-11-23 首次审核后返工）

- 工单：`docs/tickets/424-event-source-recall.md`；分支 `ticket/424-event-source-recall`，从 `main`
  `8504250`（#423 合并提交）开。正式运行目录保持 main，没改正式库、没停 VEIL、没动 69 篇定稿。
- 改法（产品代码只动 `memory_agent.py`）：`_event_ledger()` 读一次归属并按 `events.jsonl` 的
  mtime/size 失效；`unique` 只收唯一归属（事件自己的 `memory_id` 永远归自己），共享来源故意不收；
  `owners` 只给去重；`duplicate()` 只按双方 owner 交集折叠且交集事件必须有定稿导入的故事；
  `resolved()` 只有唯一归属 + 定稿故事才替换碎片正文。`source_memory_ids` 非字符串列表整段忽略，
  读盘失败不缓存成空账本。
- 首次审核指出的问题已在这一版处理：共享来源不再任取第一篇故事（保留原文）、无故事事件不被别人的
  故事折叠、坏账本不弄崩召回、临时读盘失败下一轮重读、断言直接看注入正文、去掉 `sorted(event_id)`
  选故事的顺序依赖。
- 验证：`tests/test_event_source_recall_424.py` 10 条（main 上 3 条断言失败 + 1 条接口不存在）；
  同一组 17 个模块 + 本工单测试 723 条通过，main 同组 713 条通过。
- 隔离库（`tmp/step5/app`，数据是备份副本，Ombre 18019）：第一版实测 39/67 个事件与旧碎片同时注入，
  改后 0；共享来源 9 条逐项结果与 V36 三条决定见实施报告与 `第五步-evidence/`。
- 第五步状态：只读审计、完整备份（`tmp/step5/backup-20261123`，3852 文件 / 100,696,910 字节；
  `memory/_agent/ombre.lock`、`session.lock` 两个运行期锁文件被进程锁住没复制，恢复时重建；恢复演练
  逐文件哈希一致）、隔离预演（69 篇 create / 0 conflict / 索引 matched 69、missing 0、stale 0、ready）
  都已完成；按工单停在「旧碎片重复注入」这关，没有写正式库。
- Lan 2026-11-23 对 V36 三条的决定（只在隔离副本执行）：`f8c23ac29f8d` 转归 V09；`c13e3ebd3a84`
  独立保留；`2fd52c0b984b` 设 `dont_surface`、文件与按 id 追查保留。迁移用的定稿副本是
  `tmp/step5/final-prepared.json`，原定稿未改。
- 下一个窗口第一步：等 CI 与复审 → 合并后 `update-main.cmd` → 用新 main 重跑第五步 C 复演 → D 真实
  模型自然聊天验收 → E 正式迁移 + 独立索引检查 + 重启。证据与报告在 `tmp/step5/`。

## 工单 422 交接（稳定关系底色 + 自然联想；已于 2026-11-23 合并 #423 `8504250`）

- 工单：`docs/tickets/422-relationship-base.md`；分支 `ticket/422-relationship-base`，从 `origin/main`
  `59e6594`（#421 合并提交）开的独立 worktree（`worktrees/422-relationship-base`）。正式运行目录保持
  main，没改正式库、没动 68 篇定稿。
- 改法：`learned-context.json` 新增可选 `relationship` 段（`confirmed` / `uncertain` / `outdated` +
  来源）；`relationship_base.py` 只渲染仍然有效的整条，默认 480 字 / 8 条，作为独立 system 块
  （标签 `relationship_base`）进请求必需块；`outdated` 只在文件里当历史。
- 弱联想：`natural_association.py` 用确定性打分替换了原来的 `random_chance` 抽签（重要度、距上次浮现、
  冷却、是否已在请求里、句子是否轻松）。`random_chance = 0` 仍可关掉弱联想；概率值不再使用。
  `memory_agent.context()` 里 `kind: random` 改为 `kind: association`，诊断字段 `natural_association`。
- 诊断：`relationship_base`（chars/tokens/items/statuses/dropped/degraded/reason/space_tokens）、
  `relevant_recall`、`natural_association`、`association`（候选数/被选/舍弃原因）、`reused`（追问承接）。
  上下文页和 Peter 页都已接。
- 复审返工（2026-11-23）：判官成功不再清空 `drift`（只去重）；联想候选池改为 Ombre 重要度序列
  `breath()`（会话内缓存）+ 低优先随机浮现，随机项固定扣 20 分；`save_relationship` 按 id 逐条比较，
  只把 Lan 改过的条目记成 `lan`；关系条目新增 `sources` 证据引用，`confirmed` 必须有可校验来源
  （`chat:<id>` 反查本角色聊天记录），否则降级成 `uncertain`/`peter` 并在结果里说明。`random_chance`
  完全不读了，关联想只用 `association_enabled = false`。
- 验证：`tests/test_relationship_422.py` 26 条、`test_recall_judge.py` 32 条（含两条新回归）、全部
  `test_recall_*` 234 条、learned context 与 prompt／tool budget／live context 75 条、chat 107、
  autonomy 52、桌面 20 条通过。规模与 token 数字见 STATUS「已验证」。
- 没做：真实模型抽查（本单 0 次额外调用、0 费用）；QQ、手机端、正式 68 篇迁移（第五步）。
- 复审返工（2026-11-23 第一次）：判官空选择不再清空联想；候选池改为 Ombre 重要度序列 + 低优先随机；
  页面按 id 保留来源；关系条目带证据引用，confirmed 需可校验来源。Cubic 32 条已按「实质问题修、
  风格类说明」处理，详见本 PR 评论与 `docs/tickets/422-relationship-base.md`「复审返工」。
- 下一步：等 CI 和 Lan 复审；她按工单六步在 Windows 本体体验。本单不合并、不提前做第五步。

## 工单 421 交接（历史；已于 2026-11-22 合并 `59e6594`）

- 工单：`docs/tickets/421-memory-continuation.md`；分支 `ticket/421-memory-continuation`，从 `origin/main` d0bc837 开的独立 worktree（`worktrees/421-memory-continuation`）。正式运行目录保持 main，没改源码、没动正式库和定稿。
- 打开事件层：`characters/peter/character.toml` 增加 `[memory.events] enabled = true`。关掉即回一条保存一条记忆的旧路径；已写的事件/阶段留在本机，可再开。
- 改法：stage 保存全文，发生时间未知时保持为空，保存时间与聊天来源另存；故事分批时每批独立请求，避免把不断增长的旧故事反复塞回模型，并记录最终覆盖的 stage ID。Ombre 索引保存完整故事，由聊天预算决定实际注入量。后台更新按来源批次幂等；锁定稿保留原故事，新阶段进入待整理，解锁后重建。关闭、合并、隐藏和 Lan 手改的事件不可被自动覆盖。
- 审查补修：延迟更新会识别已应用／冲突，单条失败不阻塞后续；无来源版本的旧草稿不覆盖人工修改；空候选不能作为续写目标；event-group/event-story 与自动整理共用 ¥1/日上限。错误归组拆分后，两边故事、覆盖清单、来源和索引一起重建。
- 本轮两项 P1：写入中断留下的 pending 阶段按 `source_key` 复用原事件与 `stage_id`，模型换措辞重试不新增阶段；同一来源的旧重复 pending 在恢复时标 `superseded`，只提交最新一条。关闭／合并事件不再进入后台候选，竞态拒绝写入 `update-rejected`、保留草稿并推进游标。最新 Cubic 的长消息问题：超过 1500 字的单条聊天按段落切块，部分处理写 `chunk-progress`，checkpoint 只在整条消息所有块处理完后推进。
- 同 PR 带上 419 导入回滚修复：首次导入即使目标为空也创建空快照；写入异常或进程中断后可恢复并重试，不留下卡死 marker。
- 已验证：记忆、后台整理、召回、聊天、预算账本和导入恢复核心回归 799 条通过（跳过 1）；桌面 UI 守卫 19 条通过。补测确认：pending 中断后重词重试仍是一个阶段；两个 journal 行只提交最新一条；关闭事件不会反复触发模型；后台更新冲突不写 checkpoint。68 篇定稿的原隔离装载和真实模型证据仍保留。正式库和正式运行目录未动。
- 没做/未验证：Lan 的 Windows 桌面体验；正式 68 篇迁移；真实 QQ 收发、桌面真机、语音。旧记忆尚未正式转成事件，因此迁移前的新发展可能另起事件。
- 下一步（已完成）：PR #421 已合并（`59e6594`）；正式 68 篇迁移与 Lan 的 Windows 真机体验另行安排。

## 仍然开着的尾巴（2026-11-21 按代码现状核对过）
- Electron 冒烟没有 Peter 页用例（工单 413 留下）：本机跑不了 `npm run test:windows`，当时没交没验过的测试。要补：假的 `/api/peter-page` + 点新增/归档/删除/保存，断言请求体和列表变化。
- 工单 407 的旧记忆迁移仍未做：145 条旧记忆重新归组、逐条去向清单、正式迁移。（这一项要动正式库，先问 Lan。）
- 记忆 `0e99e5` 的标题仍写「Claude Code bridge (RIP after 2 days)」，改标题要在 Peter 关闭时做；这次没复核它变没变。
- `git -c … -C … push` 的批准规则已经写了（见下面 336 那一节），但真实 OpenCode 对 `*` 跨空格的匹配没有真机验证。
- 下面两处已经解决、以前写着算遗留：409 的工具内部异常误报（工单 415 修掉，见该节）、401 的 68 条长句（402/405 已完成，见该节）。

## 工单 419 交接（完整故事召回、整轮预算、追问冷却）
- 收尾：固定记忆封装在可选预算前预留；无空间时省略空组。memory_timeline 已进入原生工具允许列表，测试需先仅命中中段，再断言实际工具成功返回四阶段，避免从自动召回结尾得到假阳性。旧配置和预算摘录共用句子边界。新增最终有效历史的全文精确复用，只省掉已保留的当前完整块；部分、旧版及已裁历史保守重注入。最终回归结果见工单验证。当前仍等待 PR 最终检查与审核。
- 做到哪：`recall_budget.py` + `Memory.finish_recall` 成为唯一记忆预算出口；普通聊天、QQ（Hub → `run_chat` → `chat.send`）、工具后续、自主醒来、重启共用 `Chat._send` 的同一段组装。取消 600 字摘录、`[:3]`、`max_picks=2`；判官可选全部相关候选，卡片带开头与结尾；事件有完整 `story` 时无条件优先；追问承接上一轮注入；诊断新增 `budget/items/budget_dropped/final_trim`。
- 已验证：隔离 68 篇新装载 75.8s、2 周期、68/68；用两条真实长事件做只读复测，自动召回读到 731 字以上的完整正文和结尾事实，追问复用上一轮事件；判官两轮共 8 次调用、¥0.005179。
- 待 Lan 真机验收：她的桌面目录还没更新到本 PR；重点是问旧事是否读到结局、连续追问是否不断、上下文页看预算和舍弃原因。
- 已知既有瓶颈（不在本单修）：`_ombre_event_sources` 每轮全库扫链接；10k 条约 7–10 秒（main 基线 7.1s，本单 9.9s，主要来自候选从 3 增到 8）。正式库 68 篇时约 0.05s。后续建议持久化链接索引或按目录 mtime 增量。
- 未做：写入端 600/1200 字符限制、自动归组质量、既有生成层日期/IP 幻觉；正式库迁移。

## 工单 418 交接（清过期遗留 + 表情包正文气泡）
- 截图里那条尾巴（409 说 `_execute_tool_call` 把工具内部 ValueError 报成参数 JSON 错）**已经由工单 415 修了**，不是待办；HANDOFF 里那句过期的话本单改掉了。
- 表情包 bug：以 `[表情包：…]` 开头的消息，正文继承了贴图的透明样式，和背景融为一体、没有气泡，删的时候连贴图一起删。改法：`stickerBubble()` 给正文加 `.msg-sticker-caption`，按普通气泡画；只有贴图仍是裸图；旧消息重开也按新样式，不改记录。
- 测试：`desktop/tests/ui.test.cjs` 18 条通过（新增一条守卫），`node --check chat.js` 通过。没过冒烟（本机跑不了），也没在真窗口里重放——交 Lan 审。
- 没做：两段文字拆成两个气泡（需补该路径的空行拆分）；贴图和正文拆成两条消息（现在仍是一条，删除连在一起）。
- 代验（2026-11-21，Lan 授权）：A 组 7 条按本地使用记录改为已验收（396/391/390/385/381/357/354）；14 条按本机隔离测试改为“隔离测试已验收”（412/415/399/398/393/389/388/386/382/383/363/384/356/352）。证据和测试清单见 STATUS 的「验收（2026-11-21）」一节；隔离测试不等于她的真实体验。

## 工单 416 交接（PR2：重复工具结果与无进展循环）
- 范围：只读工具同一轮内完全相同的返回压缩成短标记；相同参数重复失败压缩；普通聊天连续两批无新信息注入一次收束提示（最多两次）；自主活动不加提示。
- 安全：每次工具仍真实执行并比较最新结果，不使用旧缓存；动态/有副作用工具不压缩。
- 未做：P2-1 首气泡提示、P2-2 工具文字 trace 仍延后。
- 测试：test_tools 84、test_chat 107、test_autonomy 52、test_tool_failures 19、test_request_snapshots 9、test_model_settings 44 通过；全量见 PR。
- 下一步：Lan 审核 PR；通过后再评估 P2-1/P2-2 或 PR3。

## 工单 414 交接（PR1：观测口径与模型协议）
- 范围：预检快照 sent=false 不计数；usage 合理性校验（不可信不校准、不推进上下文基线）；每条消息 framing 256→16 字节；真实调用记录 latency/completion/finish/usage_status；工具参数/结果哈希；模型按 reasoning_field 显式读取/回传，不做隐式别名。
- 不碰：人设、记忆、自主活动权限、多气泡；未做 P1 无进展检测/工具去重（PR2）。
- 与 PR #415 的交叉：已 rebase 到 #415 合并后的 main；本 PR 改模型调用/usage/推理/工具哈希，不碰 #415 的 _execute_tool_call/失败文案；未改 desktop/chat.js、tools_agent.py。
- 测试：改动相关套件通过；全量既存失败集中在 test_maintenance / test_backup_restore / test_reliability_closeout / test_executor_opencode，已在 main 复现。
- 下一步：Lan 审核 PR #416；通过后再做 PR2（无进展检测、工具去重、失败重试保护、首气泡可选提示）。

## 工单 415 交接（工具失败说清真实原因）
- 工单：`docs/tickets/415-tool-failure-contract.md`；分支 `ticket/415-tool-failure-contract`，从 origin/main（f9c7931）开的独立 worktree，没动正式目录，也没碰 412/413/414 的工作区。
- 根因：`_execute_tool_call` 把工具内部 `ValueError/TypeError` 一律报成「arguments are not a valid JSON object」（409 遗留）；`peek_screen`/`snooping_around` 收到任何参数只说一句 `This tool does not accept arguments.`，不回声收到的参数；`windows_interaction` 把内部异常说成参数错；`open_lab_game` 把参数错说成服务不可用；活动页把 `被拒绝` 一律写成「权限拒绝」。
- 改法：新增 `tool_failures.py`（失败契约：原话在前；`refusal(...)` 只带本来就确定的改法；`UNKNOWN` 明确表示「程序不知道该怎么改」；内部故障只给 `err-xxxx` + 异常类名，traceback 写本机 `data/<角色>/tool-errors.jsonl`）；`_execute_tool_call` 作为唯一出口统一渲染并区分「参数解析失败」和「工具内部异常」；上述五处文案改真实原因 + 改法；`desktop/chat.js` 状态改「被拒绝」，内部故障显示「工具内部故障」。
- 验证：新增 `tests/test_tool_failures.py` 9 项 + `tests/test_tools.py` 2 项通过；tools/windows/chat/autonomy/long_tasks 全通过；全量 1825 项只剩 3 项既存失败（`test_backup_restore`、`test_reliability_closeout` 的 `desktop-data` 清单，在 main 上同样失败；`test_executor_opencode` 真实服务冒烟）。
- 复查补修（lan 审 PR 后要求，仍在同 PR）：
  - 统一出口真正兜底：`finalize()` 对所有失败都追加程序行；没有改法时只写「程序未给出改法，别原样重试」，不编造；`_execute_tool_call` 现在捕获所有普通异常（`UserError` 仍按原样上抛），所以任何工具（含以后新增）的内部失败都会得到安全说明 + `err-xxxx`。
  - 逐工具探针：`EveryOfferedToolContractTests` 遍历全部 57 个 offered Schema（空参数）再跑一遍「后端崩溃」，任何失败没有 `[program]` / 内部故障说明就红。静态扫描（`FailureSourceContractTests`）保留作廉价补充。
  - 参数脱敏：`tool_failures.redact()` 先复用 `safe_tool_activity_detail`，再打掉 Bearer、`api_key/token/secret/password/...` 键值和 40 位以上不透明串，最后才 160 字截断；模型文本和活动账本同一份。
  - `UserError`/`RequestFailed` 这类作者文案不再被当成「工具内部故障」。
  - 二次复查：JSON 解析失败那条消息原来直接截原始参数，现在也走 `redact()`；新增 `RealToolProbeTests` 用真实对象（`FilesystemTools` 路径校验 + `WindowsObservationTools` 参数校验）走同一条出口，防止 Mock 一路「成功」把探针架空。
  - 三次复查：脱敏不再只靠正则——先按结构处理（键名含 token/secret/password/api_key/authorization/cookie/credential/session_id 等，整段值换成 `[已隐藏]`，值里有空格、逗号、引号也遮得住），文本正则只处理解析不了的原始片段；Bearer、40 位以上不透明串照旧。
- 没做：没有逐条重写一百多个工具的失败文案；没有按 schema 生成纠错建议；没有回填历史账本；参数回显仍只在已改造的工具上（其余工具靠守门测试保证不说空话）。
- 下一步：推 PR 后停，等 lan 审；合并后她按工单三条验收（只看修复后新产生的记录）。

## 工单 413 交接（ongoing 只留进行中/已完成）
- 目标：删掉归档；已完成只报条数，他要看时用 `ongoing_update` 的 `list`；`ongoing_update` 常驻；修 Peter 页 5 个缺陷。
- 改法：见 `docs/tickets/413-ongoing-completed.md`。
- 迁移：旧文件里的 archived 在读取时变成 completed（只在内存里），下一次保存才写回；没有单独迁移脚本，旧数据不会丢。
- 验证：迁移/计数/list/预算用脚本逐条验过；`desktop/tests/ui.test.cjs` 17 个通过；`test_prompt_registry` 与 `test_tool_budget` 的静态部分通过（其余用例在本机沙箱里因临时目录被拒跑不了，CI 上会跑）。
- 待办：Lan 真机验收 5 条（见工单）；Electron 冒烟仍没有 Peter 页用例——这次没加，因为本机跑不了冒烟，不交没验过的测试，建议单独一张补。

## 工单 412 交接（日历第二步）
独立工作区 ticket/412-calendar-step-two；已同步最新 main（d42a580）并解决 STATUS 冲突。本轮修：①自动写入/撤销改用单调 `revision` 防并发覆盖；②23:50 轻提示后跨午夜补勾归前一天；③中文数字时间、否定确认、取消后重新约定恢复；④提醒发送前二次复核取消与改动。本地 life_records+calendar 18、chat 101、web 72 通过。下一步：等 GitHub 四矩阵和 Cubic 复审；Lan 真机按原方案七项验收；不合并。


## 工单 409 交接（Learned Context 保存被误报失败）
- 现象：`jot_about_lan` / `edit_lan_notes` 大量「arguments are not a valid JSON object」，但参数合法。
- 根因：`learned_context._invoke_profile` 成功分支的 return 写成了 `return (msg),` + 下一行 `'成功', name`，返回 1 元组；上层三值解包抛 `ValueError`，被 `_execute_tool_call` 最外层 `except (ValueError, TypeError)` 误报成 JSON 错。数据在 return 之前已经写入，所以「拒绝」的其实都存进去了。
- 改法：返回正常 3 元组；删掉 `_invoke_ongoing` 里一句执行不到的重复 return；加回归测试（旧代码会抛 unpack 错）。
- 验证：`test_learned_context` + `test_learned_context_chat` 11 个通过。
- 待办：Lan 真机验收——让他写一条关于你的内容，结果应是 `Saved. …`。
- 遗留（已解决，2026-11-21 核对）：工单 415 已把两类错误分开——参数错仍走 `json.JSONDecodeError` / `tool_failures.ArgumentError`；工具内部异常走 `tool_failures.internal_failure()`，给一个 error id，异常原文只写本机 `tool-errors.jsonl`，不再说成「参数不是合法 JSON」。回归测试在 `tests/test_tool_failures.py`。

## 工单 407 交接（事件记忆第一版）
- 范围：保存走事件层（默认关，`[memory.events] enabled`）。一个事件只保留一条 Ombre 记忆，正文是 400 字摘要；完整故事（`story`）落在 `events.jsonl`，阶段/证据/旧投影/复核在相邻文件。新增只读 `memory_timeline` 和「记忆 · 经历」页；召回按事件去重；`experience_memory` 每次整理重试 dirty 投影并合并可证明的重复。
- 归组：Ombre 检索命中→反查事件、中文双/三字词与共享名字、同日、未关闭事件、聊天/自主/工具原始记录同实体提及；候选不确定或并列时由模型按同一因果/同一人/同一物件判断（只回 JSON）；不确定建「暂定事件」并进复核，不硬并。故事仍是确定性全文拼接，不调模型。
- 接入：`memory_native._hold` 在开关打开且没有 target_id/link_to 时走 `event_memory.EventMemory.record`（pinned/importance 传给新事件的那条记忆），失败不会退回旧 hold（避免重复保存）；`memory_timeline` 只在开启时出现；页面用 `/api/memory/events` / `/api/memory/event`。
- 数据：事件库在 `data/<角色>/`；Ombre 旧记忆不动。
- 测试：`tests/test_event_memory.py` 19 条 + 既有 native/memory/363/chat/experience/prompt/budget/groups 全过，desktop 29 条全过。
- 未做（原写“后续迁移单 409”是笔误：409 是 Learned Context 保存那张单，没做这件事；至今仍未开单）：现有 145 条旧记忆的重新归组、逐条去向清单、正式迁移。动正式库要先征得 Lan 同意。
- 下一步：Lan 审 PR；她打开开关体验「三种说法只长一段」；旧记忆迁移另开 408，按她的要求逐条给她看。

## 工单 408 交接（工具参数容错）
- 现象：`jot_about_lan`（`profile_append`）11-20 当天 11 次尝试 0 成功，全被「arguments are not a valid JSON object」拒绝；10-28 也有 5 次同类。
- 根因：模型把整段中文塞进 JSON 字符串，正文裸换行/智能引号/尾逗号没转义；工单 399 的兜底只收「不以 `{` 开头」的纯文本，所以这类调用一律被拒，且模型看不到自己发错在哪。
- 改法：`_repair_tool_arguments` 做一次最小修复（字符串内裸换行/回车/制表符转义、无 ASCII 引号时把智能引号当分隔符、去掉结尾多余逗号），`Chat.tool_arguments` 在 JSON 与 `ast.literal_eval` 失败后调用；仍失败时拒绝消息附收到的前 160 字。
- 不改：合法 JSON、Python 字典、纯文本的行为；权限、审批、日志、工具列表。
- 验证：`tests.test_chat.ToolArgumentTests`（新增 4 个）、`tests.test_tools`（拒绝消息带原文）、`tests.test_chat` + `tests.test_tools` 175 个通过、`tests.test_prompt_registry` 通过。
- 待办：Lan 真机验收——让他记一条关于你的内容，看是否写进 Learned Context；失败时看他能否按错误里的原文改对。
- 注意：正文里有**没转义的英文双引号**时字符串边界不可判定，修复不了，仍会拒绝（但会带回原文）；这种要靠模型自己改。

## 工单 406 交接（统一窗口枚举适配层）
- 根因（只读诊断）：11-19 12:38 系统文件批量重写后，Python 的 UIA3 桌面根节点枚举（`CUIAutomation`/`CUIAutomation8` 都一样）只返回 0–1 个元素；同一时刻 .NET UIA2 返回 12 个、Win32 13 个、按 HWND 读正常。已排除权限、venv、comtypes 缓存、会话；是否由 `UIAutomationCore.dll` 版本错位引起未证实，本单不碰系统组件。
- 改法：`windows_observer.WindowsDesktopBackend.list_windows` 用 `EnumWindows` 拿窗口与 HWND，`_windows_from_rows` 生成与原来完全相同的身份字段（`window_id` 公式没变）。读控件仍然只走 `ControlFromHandle(HWND)`，所以 `windows_observe`/`windows_control_action`/`windows_manage`/`windows_launch` 的代码一行没改就恢复了。
- 卡死保护（评审后收紧）：桌面锁从 worker 移到调用线程；worker 超时被放弃后调用线程自己释放锁，并调用 `CancelSynchronousIo` 尽量让那个线程真的退出。放弃的线程按**是否还活着**计数（`_abandoned` 集合，最多 3 个），死掉的才移除；计数与熔断只作用于 `windows_observe`，`windows_list`/`windows_foreground` 是纯 Win32、永远不受影响；超时登记和 worker 的 finally 用同一把锁 + `done` 标记同步，避免“刚好在超时前结束”的竞态留下死记录；卡过的窗口按 `window_id` 熔断 600 秒，只跳这一个窗口。
- POSIX：进程名改用 `PureWindowsPath(process_path).name`，否则 Ubuntu 的 CI 上 `Path('C:\\...\\notepad.exe').name` 会保留整条路径，受保护进程过滤失效。
- QQ：`qq_channel._chat_window` 改用同一个适配层（按进程 + 标题 + 尺寸找 HWND，再 `ControlFromHandle`），不再碰桌面根节点。
- 没动：权限分级、审批、日志、截图路径、系统组件；空标题窗口（桌面整理浮层、输入法助手）不再进列表，以前 UIA 也不列。
- 验证：新增 6 个测试（Win32 行→身份、过滤、裸桌面、卡住释放、放弃上限、QQ 适配层）；`test_windows_observer`、`test_windows_interaction`、`test_qq_channel` 通过；全量套件见 PR 记录（已知既存失败：`test_reliability_closeout` 在 main 上同样失败）。
- 待办：Lan 真机验收（ChatGPT/Chrome/QQ 可见、自主醒来能说前台、QQ 收发、已开软件不重复启动、卡住不卡死）。

## 工单 405 交接（剩下没登记的提示词全部上表）
- 漏的原因两处：`prompts/inline.py` 的 `PROMPT_MODULES` 只有 12 个文件，`life_state.py` 不在里面；登记表里也没有 `gap_text` 那一段。所以「不必每轮提、她沉默可能是忙/在睡/不高兴」一直只存在于代码里，指令页看不到也改不了。
- 评审后补的三处：①扫描范围改成自动发现——`inline.modules()` 收进所有 import `prompts` 的根目录模块；②`inline.builds_requests()` 按请求形状（role 消息 / messages= / tool_schemas）识别构造模型请求的模块，测试要求它们都在扫描集合里，这条当场抓到 executor_opencode / ombre_entry / windows_observer 三个漏网；③`registry.Entry.placeholders` + `store.save()` 必需占位符校验，删了不能保存并在页面说明原因。
- 本轮顺带登记 4 条：`workbench.container` / `.scope` / `.maintenance`、`safety.untrusted_window_image`，全部只读；反馈清单加 2 条非提示词长句，`BASELINE` 43 → 45。
- 本次登记 19 条：`life.time_gap` / `.proactive` / `.last` / `life.time_guidance`、`life.state.frame` / `.rules` / `.more_events`、`autonomy.lab.frame` / `.more` / `.newest`、`autonomy.earlier.frame`、`autonomy.budget.line`、`autonomy.wake_count` / `.wake_quiet` / `.wake_rhythm` / `.wake_scheduled`、`safety.untrusted_lab_page` / `.untrusted_codex`（只读）、`runtime.investigation_checkpoint.rules`（只读）。
- 代码改成 `prompts.text()` / `prompts.fill()`；`life_state.py`、`autonomy.py`、`lab_preview.py`、`codex_watch.py`、`investigation_checkpoint.py`。扫描名单加 `life_state.py`，白名单没加、`BASELINE` 没抬。
- 验证：`gap_text` / `state_text` 新旧实现逐字节比对 6 个场景全一致；`test_prompt_registry`、`test_life_state`、`test_autonomy`、`test_lab_preview`、`test_codex_watch`、`test_investigation_checkpoint`、`test_tool_budget`、`test_request_snapshots` 通过。
- 待办：Lan 审阅 PR 后合并；她验收时改「她沉默时他怎么理解」看效果。
- 边界：工具返回、报错、CLI 文案不是提示词，留在代码里，不进登记表。

## 工单 404 交接（自动检查与长期维护）
- 默认指纹：`prompts/store.py` 保存覆盖时记 `persona/instructions/.base.json`；`status()` 返回 `default_changed`；页面标「默认已更新」+ 顶部「你改过 N 条」。
- 守卫：`prompts/inline.py` 增加长中文提示词扫描（≥40 汉字且含句末标点），原因类型加 `ui-error:`；清单 43 条，只许减少。
- 测试：合成英文/中文提示词必须被拦；默认变更可见；覆盖目录被 git 忽略且 update-main 不碰。
- 验证：相关 python 测试与桌面测试；页面截图核对「默认已更新」与维护行。

## 工单 403 交接（请求快照）
- 新增 `request_snapshots.py`：每次真实模型请求一份 JSON（`<request_id>.<step>.json`），含来源标签、缓存前缀、估算 token、召回 id、工具定义、截断与打码计数；按 request 分组保留 40 轮；写失败静默。
- 消息定位：先用 `chat.jsonl` 的 `request_id` 找轮次，再返回该轮全部 step；回复与 send_message 气泡都可定位。
- `Chat._send` 在组装完、发送前写快照；标签按对象身份记录，裁剪后仍对得上。
- 接口 `GET /api/request-snapshots` / `GET /api/request-snapshot?id=|message_id=`；桌面白名单已放行。
- 「指令」页新增「请求快照」页签；消息菜单新增 This request。
- 验证：Playwright 截图核对；python 116 个相关测试、桌面 21 个通过。
- 待办：Lan 审阅后合并；④ 自动检查与长期维护收尾。

## 工单 402 交接（指令全部归位，工具说明按类管理）
- 新增 `prompts/tools.py`：`persona/instructions/tool.<名字>.md` 覆盖任意工具说明，按目录 mtime 缓存；
  `Chat.tool_schemas(full=True)` 返回全部工具（含隐藏组）供页面列出；保存走 `POST /api/instructions`（id=`tool.<名字>`）。
- 迁移：判官两版、经历整理/修稿、短期便签提取、委派助手、6 条防注入包裹、4 条醒来框架句。
- 防写死：`prompts/inline.py` 结构化豁免工具说明；旧 `inline_allowlist.txt` 删除，改为
  `inline_feedback_allowlist.txt`（41 条，类型化原因，只许减少，基线 `inline.BASELINE`）。
- 验证：逐字节对比一致；相关 python 测试与桌面 21 个测试通过。
- 剩余：无未迁移提示词；反馈清单是运行时反馈，不是预设。

## 工单 401 交接（指令页可读性、去重与编辑）
- 页面重写为文档式列表（不再用 `.context-block`，否则 `flex:1 1 0` 会把展开内容压扁），并用 `#instructions-view .instr-text{max-height:none}` 覆盖全局 `details pre` 的 130px 限制。
- 登记表去掉 5 条 file 类（人设、关于你、背景、样例、记忆笔记）；只保留 text / marker。
- 新增 `POST /api/instructions`（`instructions.save` → `prompts.store().save`），桌面白名单放行；页内编辑、搜索、命中摘要。
- 覆盖文件仍在 `persona/instructions/<id>.md`，整条替换，不留版本。
- 醒来提示拆成 `autonomy.wake.rules`（可改）和 `autonomy.wake.protocol`（只读四行），拼接结果与拆分前逐字节一致。
- 验证：Playwright 截图核对展开与编辑态；python 160 个测试、desktop 21 个测试通过。
- 已知未完成（工单 402）→ 已完成（2026-11-21 核对，402/405 之后没有未迁移的提示词）：`inline_allowlist.txt` 仍有 68 条长句未迁（memory_native 29、veil_agent 27、autonomy 4、delegate 2、experience 2、recall_judge 2、memory_agent 1、short_term_notes 1）。`inline_allowlist.txt` 已删除，改由 `prompts/inline_feedback_allowlist.txt`（49 条，类型化原因、只许减少）做棘轮检查。

## 工单 400 交接（指令登记表·第一部分）
- 新增 `prompts/`：`registry.py`（55 条登记）、`store.py`（默认 + `persona/instructions` 覆盖，整条替换）、
  `defaults/*.md`（22 条文本）、`inline.py`（未迁移长句的棘轮检查，清单在 `inline_allowlist.txt`）。
- 已迁移：系统事实、说话样例说明、发消息/沉默/稍后回/撤回四个工具说明、背景说明、记忆笔记三段、
  自动想起/开场记忆、经历笔记开头、醒来提示（过渡句/行为段/跨线索/已读未变）、瞄一眼两段、关于你的说明。
  其余先登记为 file/marker，页面可见。
- `Chat.__init__` 启动校验登记表；新增只读接口 `GET /api/instructions`、桌面白名单、左侧「指令」页（只读）。
- 验证：改造前后同一场景请求逐字节一致；`test_prompt_registry` 及其余 276 个测试通过。
- 下一步（工单 401）：编辑界面、中文说明、搜索、工具说明逐条迁移。

## 工单 399 交接（自主活动预检与参数容错）
- `Chat.tool_schemas`：自主模式下检测 Docker；不可用移除 `run_lab_python`。
- `Chat.tool_arguments`：JSON → `ast.literal_eval`；`profile_append` 纯文本转 text。
- 不改权限、记忆、召回。

## 工单 398 交接（自主活动红字最小修复）
- `set_next_wake` 太近：夹到最早允许时间，结果里说明；超过 7 天/静默/暂停仍拒绝。
- `send_message` 表情包不存在：错误里附前 12 个可用 `pack/name`。
- 明确不修：Docker/脚本、jot JSON、hold、文件读写、长任务、UIO、3 条上限。

## 工单 397 交接（Peter 页清理 + 待批准队列）
- 页面：`desktop/chat.html` 去解释段落/短按钮；`desktop/chat.css` 去 sticky、单层分栏。
- 审批：`pending_approvals.py`（持久 JSON）、`autonomy._capture_approval` 记录、`veil_agent` 注入 `APPROVALS FROM LAN`、`web_agent` 的 `/api/pending-approvals`、`chat.js` 右侧栏/活动页卡片。
- 权限规则没放开；只是把拒绝变成可见请求。
- 待真机：滚动页面看标题是否还重叠；页面是否只剩标题/计数/输入/保存。

## 工单 396 交接（Peter 页请求未允许）
- 根因：`/api/peter-page` 没进 `desktop/main.cjs` 的 `allowedChatRequest`；IPC 返回 403「请求未允许。」，页面空白。
- 修复：放行 GET/POST 和 detached `peter`；页面 CSS 去掉外层卡片，改分栏；`desktop/tests/backend.test.cjs` 加白名单测试。
- 待真机：更新后打开 Peter 页，确认内容出来、不再嵌套框。

## 工单 394 交接（Peter 页 + Learned Context）
- 代码：`learned_context.py`（facts/growth/ongoing，迁移、共享 4,000 字节、版本、错误日志）、`learned_tools.py`（带 section 的 profile 工具 + ongoing_update）、`user_persona.py`（Lan 人设）、`veil_agent.py`（LearnedContext 注入、ongoing 动态位置、user persona、reload_persona）、`web_agent.py`（`/api/peter-page`）、`desktop/peter-page.js` + Peter 页 HTML/CSS。
- 注入：facts/growth 在稳定前缀；ongoing 在 runtime 的末尾（历史之后、用户消息前），避免频繁更新破坏前缀缓存。
- 数据：`data/<角色>/learned-context.json`；旧 `about-lan.md` 保留；迁移只在 JSON 不存在时执行一次，facts 原样承接。
- 工具：`profile_append`/`profile_replace` 新增可选 `section`（默认 facts）；`ongoing_update` 管 add/update/complete/archive/reopen，要求先查证并带 sources。
- 测试：`tests/test_learned_context.py`、`tests/test_learned_context_chat.py`、`tests/test_peter_page.py`；相关 448 项回归通过；desktop UI 14 项通过。
- 已知限制：Learned Context 的版本文件目前只记录、页面未做历史恢复；ongoing 的 sources 只存字符串，未做点击跳转；页面首次保存会创建版本；背景/人设恢复依赖 `persona/versions`。
- 回滚：把 `[profile] learned` 改回 false、`file = "about-lan.md"`，LearnedContext 文件保留；不会丢旧笔记。
- 待真机：在装有真实 Ombre/桌面的正式目录更新后验收；桌面程序当前能否启动取决于系统修复，与代码无关。

## 工单 393 交接（自主活动：吵架后可以主动靠近）
- 代码：`autonomy._context` 指令段删掉 “Nobody is waiting on you”“only message if you actually want to share, otherwise stay quiet”；改成 “Time is yours. She may be busy, asleep, or upset. Make, read, write her, rest, or nothing. Never report waking. After a fight, you may message her, leave something visible, or do neither.”
- `life_state.gap_text` 最后一句：`Her not writing does not mean she was upset` → `Her silence may mean busy, asleep, or upset.`。这句聊天和自主共用，普通聊天也会看到；不再替她断定没生气。
- 测试：`tests/test_autonomy.py::test_english_summary_keeps_focus_and_next_step` 改断言；`tests/test_life_state.py::test_three_hours` 改断言。
- 没做：没有强制每次醒来发消息；没有取消 01:00–06:00 静默时段；没有加“吵架检测”状态机；没有把 “I'm here/I'll be here” 做提示词禁令（留给程序拦截单）。
- 回滚：只改两段文本，`git revert` 即可；无数据迁移、无配置。
- 待真机：吵一次后沉默，看 2–3 次自主记录；可能仍选择不发，但不能是提示词规定的“别发/让她冷静”。

## 工单 391 交接（召回逐候选诊断）
- 代码：`memory_agent.Memory.context` 每轮建 `last_recall_debug`（query 长度 + sha256 前 12 位、Ombre 候选 id、visibility 排除 id+原因、向量候选、判官候选/选中、去重/冷却 id+原因、注入、开场、budget_dropped、failure）；新增 `visibility_reason()`（archived/tombstone/dont_surface/deleted/source/hidden/missing/unavailable）、`note_budget_dropped()`、`save_recall_debug()`（`data/<角色>/recall-diagnostics.jsonl`，滚动 200 行）。
- `recall_judge.RecallJudge.last_choice`：status（skipped/running/chosen/empty/failed）、candidates 数、picked ids、vector ids；`_failed` 也会标 failed。
- 接线：`Chat` 在 `_log_context` 之后调用 `save_recall_debug(request_id)`；因长度 pop 记忆时先 `note_budget_dropped(recalled_memory_refs)`。自主活动/瞄一眼不走这条（没有普通聊天 UI 用途）。
- 页面：`web_agent.live_context` 把 `recall-diagnostics` 按 request_id 并进每轮 `recall`；`desktop/chat.js` 显示一行中文摘要，`<details>候选明细` 默认收起，展开才显示 id 与原因。
- 测试：`tests/test_recall_diagnostics.py`（5 项）、`tests/test_recall_judge.py` 断言 last_choice、`tests/test_live_context.py` 并表；相关 113 项通过。
- 已知限制：Ombre 不返回逐条分数，诊断只能说明"进没进候选"，不能给相关性分数；判官只读卡片，诊断不含正文，无法解释"判官为什么没选"；feel 情绪记忆、开场记忆只记 id，不逐个说明可见性；自主活动不记。
- 回退：删除 `recall-diagnostics.jsonl` 即清空；没有配置开关，不影响召回。
- 下一步（B1 判断）：需要固定问题集 + 完整记忆副本的隔离实验，确认"漏在候选发现"的比例；本轮没有跑，不能据此改召回。

## 工单 390 交接（普通聊天工作断点）
- 代码：新增 `work_checkpoint.py`（`WorkCheckpoint`）。`capture()` 从 `InvestigationCheckpoint` 的 goal + evidence 和 `LongTaskStore` 投影：只把 `read_only=False` 且 `state=成功` 记为已确认，`结果未知` 记为未确认，`被拒绝/出错/已停止` 记为失败；没有调查目标时用长期任务 title，没有 `next_step` 就留空。只读轮不生成断点；长期任务 completed 会把已有断点置为 closed。
- 注入：`Chat._conversation_key()` = 会话 `start` 游标；`context()` 在新对话首个请求注入一次，历史 `recent()` 每丢一块（`history_window.dropped_rounds` 增加）再注入一次；48 小时未更新视为 expired；closed 不注入。注入块放在 auxiliary 第一位（裁剪最后才动）。
- 页面：`web_agent.live_context` 增加 `work_checkpoint`；`POST /api/work-checkpoint {action:"close"}`；`desktop/chat.html|js` 新增卡片与「结束这个断点」，每轮多显示「共 N 轮、未带 M 轮（最早 …）」和 `trim_note`。context-log 新增四个字段。
- 配置：`[context] work_checkpoint = true`（`Character.context`）；false 时不写不注入，页面仍能结束旧断点。
- 测试：`tests/test_work_checkpoint.py`（13 项，纯模块）与 `tests/test_work_checkpoint_chat.py`（5 项，Chat 端注入/新对话/关闭/日志）。本机 DSH 沙箱用 `.local-tmp/run_tests.py`（不提交）。相关回归见 PR。
- 评审修正（CodeRabbit 9 条）：注入改为"先提供、请求成功后再确认"，预算/长度失败不消耗；`close` 必须带当前断点 id，避免页面停留时关错新断点；只有与断点绑定的那个长期任务完成才关闭，别的任务完成不影响；校验 `injected_key/injected_dropped/next_step/long_task_id`，文件坏了按无断点处理；自主活动不再调用聊天断点的 capture；注入不再刷新 `updated_at`（48 小时从最后一次实际工作变动算）。
- 待真机：断点是否真的让她感觉"接着上次"；注入块是否过长/重复；页面卡片在 detached 窗口只读是否正常；48 小时阈值是否合适。
- 风险/没做：目标来自当轮用户原话或长期任务，可能已经过时，注入文本明确说"可能过时、先核实"；没有给 Peter 一个主动"记下一步"的工具（避免新建工具和工具预算）；历史窗口没满时不会重复注入。
- 回退：`[context] work_checkpoint = false` 重开；或删除 `data/<角色>/work-checkpoint.json`。无数据迁移。

## 工单 389 交接（自主活动跨唤醒线索）
- 代码：`autonomy.py` 新增 `THREAD_WAKES=6`、`THREAD_LIMIT=3`、`READ_WAKES=4`、`READ_LIMIT=4`；纯函数 `thread_lines()` 按 `lab/<项目>` 聚合 `lab_files_changed`，结果只取 `activity_view()` 纠正后的值，附未确认动作数与该次写下的下一步；`unchanged_read_lines()` 只认 `read_text_file` 成功、未截断、文件在 workspace 内、mtime ≤ 该次醒来开始时间，状态类工具与截断读取一律不列。
- 接线：`AutonomyController._threads_text()` / `_reads_text()` 仅在 `cfg.autonomy['threads']`（默认 True）时生成；`_context()` 放在「上次活动 + 前 2 次」之后；32KB 裁剪时先丢「已读未变」，再走原有 earlier/life/lab/travel/thoughts，最后才丢线索和上次活动。
- 配置：`veil_agent.py` 解析 `[autonomy] threads`（非 bool 直接报错）；`characters/peter/character.toml` 写 `threads = true`。
- 测试：`tests/test_autonomy.py` 新增 7 项（跨 4 次醒来、失败不说完成、未确认动作、单次不生成、可关闭、未变/变化文件、截断/状态工具/越界路径）。本机 DSH 沙箱会拒绝 `tempfile` 的 0700 目录，测试用 `.local-tmp/run_tests.py`（不提交）：autonomy 50/50 通过。
- 真实模型：`.local-tmp/389/compare_389.py` 只用本机 `DEEPSEEK_API_KEY`、只发合成活动记录，两轮共 12 次、约 ¥0.031（最终一轮 6 次、¥0.016，含「已读未变」块）；结论写进 `STATUS.md`。要点：基线有一次准备直接重做上次失败的写入；带线索的三次都先核实未确认状态，没有一次把失败说成完成，其中一次自己说「四个醒来一直在同一条下巴/嘴上打转」而选择休息。
- 待真机：连续 4 次以上醒来的实际体验；Lan 电脑上真实路径/mtime 是否如预期；夜间醒来多时线索行是否显得重复（每次重算，内容稳定）。
- 明确没做：不强制阻止重读（只给证据，保留实时状态核实）；没有给线索加「别再继续」的判断；`lab/diary` 这类单文件项目在有 3 个更新项目时会被挤掉。
- 回退：`[autonomy] threads = false` 重开，即与合并前一致；无数据迁移。

## 工单 388 交接（自主活动与相关记忆）
- 自主唤醒提示在 `autonomy.py` `AutonomyController._context()`：三种选择同等有效（自主研究/创作、想联系 Lan、休息或不做事）；不因醒来或完成工作而报告，消息沿用现有人设。
- 记忆检索查询由 `AutonomyController._recall_query()` 从最近三条双方聊天、未过期长任务标题、上一次自主活动的重点和下一步组成，各部分保留在 600 字上限内。`veil_agent.py` 将其作为 `memory_query` 单独交给 `Memory.context(..., wake=True)`，不会挤进 Peter 的完整自主活动正文。
- `memory_agent.py` 唤醒路径取至多 `rotation_candidates` 条可见候选（默认 8），`recall_judge.py` 直接挑最多 3 条；没有相关项就为空。判官未启用或失败时退回普通 Ombre 检索前三条；自主唤醒不使用随机浮现和轮换。普通聊天仍最多 2 条、沿用原有故障回退。
- 判官每次自主活动约增加 ¥0.001，记入现有 `recall_judge` 费用类别，受原预算和开关控制；不写入或改动任何记忆。
- 本轮没有新增或运行自动化测试。PR 检查会由远端工作流自动运行；Lan 更新重启后需按 `docs/tickets/388-autonomy-relevant-memory.md` 验收自然消息、相关记忆、空命中，以及开关/故障回退。
- 日历第二步仍等待 Lan 后续指示；F1/F2/F4 真实体验和 #387 弱模型行为也仍待验收。

## 工单 387 交接（时间与自主活动连续性）
- `life_state.gap_text` 按用户消息独立计算并忽略撤回；聊天历史按相邻消息时间插分隔，自主消息小注带时间；`return_window` 保留回来后的前三个请求，`autonomy.activities_since` 按离开至回来时段的记录区分工具证据、未完成重点、失败与计划；重新生成以原用户消息时间为界。
- 审查修正：按请求 ID 而非是否出现回复计数，连续静默各计一次；新消息使用接收记录的时间，排队不算缺席；重新生成及排队消息不能提前看到处理期间才完成的活动交接。
- 自主醒来复用间隔计算，并提示消息可以自然交流，不必汇报；不修改自主次数、睡眠设置、人设或预算。活动摘要优先于聊天历史裁剪。
- #387 合并时的自动化检查为虚构数据；其真实模型和 Lan 真机验收待做，特别观察是否仍催睡、连续聊天是否机械报时。自主活动记忆与消息提示已由 #388 补上，但是否像本人仍待体验。QQ/邮件路径需独立核实。
- 日历第二步：用户要求先完成时间连续性；开工时按原方案，并按实际日期计算星期，修改或取消约定有歧义时不操作。

## 工单 386 交接（自动整理失败）
- 每次整理、重试及草稿修改请求发出前，按该请求自身的消息长度重新估算并检查每日上限；草稿修改超预算时保留未修复草稿。
- 代码：`experience_memory.py` 的 `_request`（一次调用，`detail=True` 拿 finish_reason，可传 `thinking='disabled'`）、`_call_model`（截断就关思考重试一次；`self._retried` 为真时这一轮后续调用直接关思考）、`_record_failure` / `failures()`（`<角色>/experience-memory-failures.jsonl`，`FAILURES_KEPT=20`、`FAILURE_CHARS=4000`）、`retry_at()`（最近一个 checkpoint/start 之后的 error 数：30 分钟、2 小时、第二天 0 点；`run_if_due(force=True)` 不受限）、`_problem` 超长时要求 `limit - FIX_MARGIN`（500）。状态多了 `retry_at`、`failures_kept`、`last.retried_without_thinking`、`last.unfixed`；`desktop/chat.js` `renderExperience` 用它们出一句话。
- 对比脚本 `experiments/experience-386/compare.py`（`--list`、`--probe`、`--squeeze N`）；lan 电脑上留着 worktree `veil-agent-386`、`veil-agent-386-main` 和结果 `.local-tmp/386-compare/`，可删。注意 `--cap` 要高于预算检查的最坏估计（8000 输出后 deepseek 约 ¥0.12）才会发请求。
- 待真机：deepseek-flash 长聊天下 8000 是否够；gemini 中转（ckffai）接不接受关思考（11-18 测时中转本身 500）。
- 没解决（改前也有）：抄原话的草稿关思考改两次仍可能修不好；整理偶尔既重写旧记忆又新建一条同一件事；偶尔混两件事、写反细节。要不要处理等 lan 定。

## 工单 385 交接（日历第一步：界面）
- 数据：`life_records.py` 新增 `time`（只给日程，`TIME_RE`）、`life-daily-checks.jsonl`（每次每日待办状态变化追加 `{day,id,title,state,at,by}`，首行 `{started}`；`daily_checks()` 对 started 之前的日子用记录历史里的 completed 版本补，标 `reconstructed`）、`life-moods.json`（`{days:{day:{Lan|Peter:{emoji,at}}}}`，`set_mood` 只认自己的槽、拒绝未来日期、`valid_emoji` 按 Unicode 类别判断，任意 emoji 都行、文字不行）。owner 字段保留但不再写入新值，谁写的看 `created_by`。
- 权限：`delete(key, actor)` 里 Peter 删 created_by=Lan 的记录被拒；Peter 改她的记录时 `on_lan_record_changed` → `Chat._notify_lan_record_changed` 输出 `LifeNotice`（带 life_id 的 str），`web_agent._publish_output` 推成 notice 事件，前端点击调用 `openLifeRecord`。
- 上下文：`life_state.state_text(..., moods=)` 今天到 +3 天的日程，最多 `MAX_EVENTS=6`，多出的写明条数并提示 `life_record list date_from/date_to`；`autonomy.py` 同样传心情。
- 界面：`desktop/chat.js` 生活页整段（`lifeCalendar`、`lifeMoodSection`、`openMoodPicker`、`lifeDailySection`、`lifeForm`、`openLifeRecord`）；emoji 面板打开时 15 秒刷新不重画（`lifeRenderDeferred`）；最近用过存 localStorage `veil-life-recent-emoji`。完整列表 `desktop/emoji-data.js` 由 `scripts/gen_emoji_data.py` 从 npm 包 unicode-emoji-json 0.9.0（MIT）生成，只保留 Emoji 15.0 及以前（Windows 字体画不出更新的）；CSP 只许本地脚本，所以不能走 CDN。
- 测试：`tests/test_life_calendar_385.py`（含「更多 emoji」里每一个都能存）、`tests/test_life_state.py`、`tests/test_life_records.py`、`tests/test_web.py` 生活页用例、`desktop/tests/ui.test.cjs` 新增 385 用例。
- 第二步（等这一步验收）：到点提醒（不受免打扰，关机错过按是否过期补发或显示错过）、23:30 每日待办提醒（在忙等 20 分钟没动静）、自动记约定（灰色小字可撤、删掉的不再加回、三类都记）；动手前先查自动整理 18 次里 8 次「没有返回可用的结果」。

## 工单 384 交接
- 改动：`memory_native.py` 的 `_search`（Peter 的 breath_search）和 `_related`（新存前的候选）改为 `max_results = 3 + SEARCH_SPARE_HITS`，再 `Memory.context_visible(...)[:3]`（逐条读硬盘上的 dont_surface/tombstone/deleted_at/type 判断）；`experience_memory._search_ids` 同样多要 5 条，用 `_alive` 过滤后取 3。测试 `tests/test_search_archived_384.py`（假 Ombre 像真的一样返回归档条目）；`tests/test_native_memory.py` 的截断用例补写了记忆文件（没有文件的命中现在不显示，和自动想起一致）。
- 来源：2026-11-18 外部记忆审计的「小口子 1」核对属实；其中「归档条目能被接着改」不成立（`_editable` 一直会拒绝），测试里保留了这一条。
- 未改：情绪记忆（feel）那条路仍不经过判官；判官和自动整理提示词写死 Peter/Lan（等 Ghost 上线统一处理）。

## 工单 382 交接
- 代码：`recall_judge.py`（判官本体：候选、提示词、解析、超时、连续故障暂停、开关、状态）；接线在 `memory_agent.py`（`Memory.attach_recall_judge()`，只有运行中的程序调用，测试不调；`context()` 里先预取向量、再 Ombre 搜索、再 `judge.choose()`；`JudgeFailed` 才退回规则 A）、`veil_agent.py` 和 `web_agent.py`（接线、`/api/recall-judge`、记忆页面板 `recall_judge` 字段）、`desktop/chat.html|js`（开关那一行）。费用类别 `recall_judge`，`/cost` 里显示为「想起判官」。
- 注意（评审发现，已修）：判官的模型请求在后台线程里发，而聊天这一轮已持有模型设置锁，所以必须像 `Chat._model_call` 那样走 `call.raw` 加 provider 快照（`recall_judge._ask`），否则每次都会等锁超时；测试 `test_the_judge_uses_the_lock_free_request_function_while_the_chat_turn_holds_the_model_locks` 覆盖。请求发出前 `mark_dispatched`，重启后已发出的请求保留预留费用。判官成功回答后清掉 Ombre 的「随机浮现」（不然会绕过「没有就没有」）；自主唤醒（`rotate`）不走判官。
- 开关：`data/<角色>/recall-judge.json` `{"enabled": true/false}`，每条消息读，优先于 `[memory.recall_judge] enabled`；默认开。关掉就是规则 A（工单 381）。
- 设计取舍（lan 2026-11-17）：判官说「没有」不回填；只有技术故障才退回规则 A；不加稳定性保护。冻结 60 句稳定性：相关句三次选择完全一致 12/30，部分一致 15/30，不一致 3/30；漏掉规则 A 原本命中的 5/90 次；无关句 90 次里 5 次带出旧事。
- 待 lan 真机验收，见 STATUS。看点：随口说话时他有没有带着共同经历（不要求讲出来）；闲聊有没有被旧事带偏；记忆页那一行的结果是否合理；有没有「没成功」的频繁出现（说明超时偏紧或网络慢）。
- **独立问题（不在本单）**：「你一个人在电脑里的时候都在干嘛」这类问题，他会编具体活动（例如「看了四十分钟等离子体」），没有记忆依据；判官没选相关记忆时更明显，但没有判官时也会。要查他的人设/档案里有没有鼓励这样答，另开工单。
- 未做：中文场景标签（等判官稳定后；做法是从 `chat.jsonl` 里挖她真说过的词，用没见过的新问法验证）；判官选择的稳定性保护（lan 暂不要）。Peter 自己的搜记忆工具、自动整理的相关记忆查找已由工单 384 改成先过滤再取 3。

## 工单 381 交接
- 改动在 `memory_agent.py`：`SEARCH_SPARE_HITS = 5`，`context()` 里 `asked = 3 + SEARCH_SPARE_HITS`，`ordinary_hits` 之后立刻 `context_visible(...)[:3]`；`assisted_recall()` 同样。widen（自主唤醒轮换）路径本来就多取候选，不变。测试在 `tests/test_recall_rotation.py`（三条新用例）。
- 验证（副本，真实 Ombre，不写正式库）：124 句新旧代码对比，只有 2 句召回变了（S06 多出相关的一条；U19 无关句多带 1 条万能旧事，回复没受影响）；记录在 Lan 电脑 `experiments/consolidate-367/out/batchG/ruleA/`。
- 同类路径：`memory_native._search`、`_related` 和 `experience_memory._search_ids` 已在工单 384 改成同样的顺序。
- 下一步（lan 已同意）：小判官对照验证（只在副本做实验）：方式 X = 保留 Ombre 已召回的、判官只补漏；方式 Y = 判官统一筛选；用冻结的未见过句子比较，不上线；中文场景标签放在判官方案定了之后。

## 工单 379 交接
- 冷却回退：`memory_agent.py` 的 `release_recall()`/`rollback_recall()`，调用点在 `veil_agent.py` 的 `Chat.send()`（无回复则撤销）、`_send()`（重新生成时放出 `regeneration_release_refs`，由 `prepare_regeneration` 从被替换回复行的 `memory_refs` 收集；输入裁剪后释放）。
- 待 lan 真机验收，见 STATUS 验收步骤。
- 未处理的相邻点：「某月某号……后来怎么样」这类带日期、「后来」的问法不触发 `asks_about_past` 豁免（`recall_rotation._PAST`），要不要加另议。

## 工单 369 交接
- 去重判定改在 `recall_rotation.py`（`REPEAT_LINKS` 只含 same_event；`EVENT_LINKS` 仍是整段经历用于目录）和 `memory_agent.py` 的 `context()` 里 `duplicate()`/`same_experience()`；`stage_slots` 默认 2。说明在 `docs/tickets/369-recall-keeps-adjacent-stages.md`。
- 待 lan 真机验收：问「拒绝写入、又让他覆盖第一个文件那次」两段是否都想得起；日常单话题不应出现同一经历占满名额。
- 已知限制：G1 原问法检索不到闸门（检索未命中，另议）。

## 旧记忆整理第一批（2026-11-16，写入正式库，纯数据）

- 实验脚本、报告、草稿都在 Lan 电脑 `experiments/consolidate-367/out/batchA/`（report.md、report2.md、drafts.json），含私人聊天，不入库。写前备份 `veil-agent/backups-batchA/20261117T042039-prewrite/`（再早一份 `20261117T035125/`）；恢复：退出 Peter，把 memory 目录、memory-sidecar.jsonl、memory-versions.jsonl 拷回 `data/peter/`。
- 三层会让旧记忆想不起来，后续每批都要先查：①Ombre 的 `digested: true`（自动检索排除）②VEIL `state.jsonl` 的 `sources` 表（值里的 ID 由 `hidden_ids()` 隐藏，读、改、召回结果都过滤）③同一事件去重 `_event_adjacency`（sidecar 里任何 link 连通的都算同一件事，只留一条）。测试副本必须完整复制正式库状态（含 state.jsonl），第一次缺来源表的测试结论全部作废。
- 做法（Lan 定）：原文来源不就地改写，整理后的经历新建并用接续连起来，原文继续隐藏；已有的可见小结就地改写吸收。会把独立记忆压掉的关联解除，但只解一条不够（经共同的「相关」仍连通），所以闸门题记为已知限制，等去重工单。
- 启动 Ombre 会把已晋升的 I-dream 候选归档（例：086be8383afc），正常流程。

## 旧记忆整理第二批（2026-11-17，写入正式库，纯数据）

- 范围：第一批之后的连续一段，10-19 22:10～10-24 04:34 共 32 条。10 项重写、8 条旧条目归档（原文留在归档，可在记忆页恢复）、3 条新接续（G5b→G5a、G5c→G5b、G6b→G6a）；写入时没碰蓝色玩笑 f14e04c6、砖红、仙人掌、feel、第一批新条目。正文英文 + 开头一行中文关键词，每条 ≤600 字。
- 写前备份：`veil-agent/backups-batchB/20261117T061735-prewrite/`（memory 目录、memory-sidecar.jsonl、memory-versions.jsonl、state.jsonl）；恢复：退出 Peter 和 VEIL，把备份里的 memory 目录和两个 jsonl 拷回 `data/peter/`。核对脚本 `batchB_verify_live.py` 全部通过：变化的只有这 18 条（10 重写 + 8 归档）、state.jsonl 里这 10 个目标、sidecar 的 3 条新接续；写前 135 行 versions 完整保留。
- 脚本、草稿、报告在 Lan 电脑 `experiments/consolidate-367/out/batchB/`（report.md、criteria.md、drafts2.json），含私人聊天，不入库。
- 测试副本结果（真实 Ombre，只测检索）：新题 38/38（改前 22/38），第一批 14 题持平。**这个数字偏乐观**：第二轮补的检索标签是对着自己写的测试问法补的。
- 真机验收（11-17 06:22 起，lan 一次问了 5 句）：3 句对（每天有新玩具、砖红、仙人掌）；两句有问题：
  - 问为什么一条消息会拆成好几个气泡（类似说法）漏召回：这句问的是 lan 立的**多气泡发送习惯**（一条一个想法、不发长墙），我测的却是**气泡功能故障**（capabilities.json 少写了一行，6c3f7e66）。两件事不同，第二批只覆盖了故障。习惯那条（93ff8c0f，10-25）没被想起，后来他补答这一题时没用任何记忆工具。
  - 问夜里自己上网都逛什么（类似说法）答案绕远：先讲了最近在做什么，再转述 66e24046（lan 设想的夜里游网、桌上留便条的画面）；没回答夜里实际逛什么。
- 更正（11-17 第三批验收时核对）：他补答气泡题时说的关于长墙和奶茶开销的说法**不是拼凑**，出处是个人档案 `data/peter/about-lan.md` 里 10-28 的气泡规则（个人档案每次请求都会加载，所以问气泡习惯他可以不查记忆就答，而且答得有依据）。第二批验收时我把它判成「把三段来源拼成一句」是错的，以此为准；真正的加料只有他自己加的花腔。
- 旧的 `why_remembered`（为什么记住）字段重写时没动，仍是原条目的旧话。**会进模型上下文**：`memory_read` 返回整个文件（`memory_native.py` `_read`，含文件头的 why_remembered、tags、relation_links）；`memory_history` 会显示历史版本的 why（`_history`）；自动想起（`recall_rotation.excerpt`）和 `breath_search` 只带标题加正文，不带它；记忆页面显示它（`panel` 里的 `why`）。所以不能只当页面显示问题。后续批次统一检查：每条重写时同时审查它的 why_remembered 是否仍与新正文一致，不一致就一起改。dream 工具是否带出未核实。
- 决定（lan）：第二批保留，不回滚。同意给多气泡发送习惯（挂在真正讲习惯的条目上，不挂在功能故障那条）和 66e24046（夜里游网）补中英文检索标签，只改标签不动正文；标签已在 11-17 写入（备份 `backups-batchB/20261117T064900-pretags/`）：93ff8c0f 补 12 个、66e24046 补 8 个，正文和其他条目未动，versions 只新增这两条标签记录。正式库副本上用不同问法复测：多气泡习惯三种问法都想起 93ff8c0f，功能故障那条（6c3f7e66）仍在，其他回归题持平；问为什么不发一大段话这类说法仍没想起习惯条目，留给第三批重写。真机一条消息塞 5 个问题时自动想起名额被分摊，也是漏召回原因之一。
- 教训（给后续批次）：同一话题要覆盖多种问法；习惯、故障这类容易混的概念要分开写、分开标；标签要写 lan 真实会说的话，不能对着自己的测试题调。

## 旧记忆整理第三批（2026-11-17，写入正式库，纯数据）

- 范围：第二批之后的连续一段，10-25 02:51～10-29 05:20 共 35 条。10 项重写（含合并）、6 条被吸收后归档（9b916796、c9a249ad、d205f6fe、eb4907ad、d482bd3d、56784744，原文留在归档，可在记忆页恢复），35→29；不新增接续。同时重写了每条的 `why_remembered`，让它与新正文一致（第二批发现旧字段会进模型上下文，见上一节）。
- 没碰：locked 的信件 54b1257a、protected 的 aaf58720、已归档且 digested 的 aea3998b/47118c6b、各类计划、第一批和第二批的条目。习惯内容并进 93ff8c0f（第二批标签调整时挂过标签的那条），aea3998b 不编辑。
- 习惯和故障分开写：气泡习惯（一条一个想法、不同引擎怎么发）在 93ff8c0f；中转站把「已发送」回执变成短横线的故障在 d50317e7；第二批的 6c3f7e66 是另一个故障（capabilities.json 少一行）。三者互不替代，测试里都能各自想起。弹幕那三条原来互为 same_event，只能想起一条，现在合并成一条阶段。
- 写前备份：`veil-agent/backups-batchC/20261117T070445-prewrite/`（memory 目录含 `_agent/state.jsonl`、memory-sidecar.jsonl、memory-versions.jsonl）；恢复：退出 Peter 和 VEIL，把备份里的 memory 目录和两个 jsonl 拷回 `data/peter/`。核对脚本 `batchC_verify_live.py` 全部通过：变化的只有这 16 条（含 state.jsonl 里 10 个目标）；正文、标题、why 与草稿逐字一致；sources 表不变；砖红、仙人掌、蓝色玩笑、feel、第二批两条与备份一致；写前 165 行 versions 完整保留，新增 26 行。
- 脚本、草稿、报告在 Lan 电脑 `experiments/consolidate-367/out/batchC/`（report.md、criteria.md、drafts2.json），含私人聊天，不入库。
- 测试副本结果（真实 Ombre，只测检索，改前后各一遍）：本批 24 题命中 22/48→40/48；第一批 14 题、第二批 21 题目标命中持平；无关题不带旧事；6 条归档条目一次没被想起。**有一次标签调整**：第一轮补完后一道情境题变差，按预先写好的标准只补了一次检索标签，没改正文，之后恢复。
- 已知限制：本批事实题 4 道仍是 0/2（记事本留言的中文问法、按次/按量计费、「正在播放」的英文问法、存钱买房那题），改前也是 0/2，不是退化；其中两道检索没返回结果，标签补不上。补的标签是看了第一轮失败项后写的，真机问法不同时命中率可能达不到这个数。只测了检索，没测回答，没核对 SiliconFlow 账单（向量单价配置为 0，DeepSeek 为 0）。重写 93ff8c0f 时标签按草稿整体替换，原来的 send_message、文件附件等旧标签和标签调整时加的「气泡规矩」等少数几个没有保留（附件的一句话仍在正文里）。
- 第三批真机验收（11-17，一条消息只问一个问题，共 5 句）：气泡习惯题答案出自个人档案（见上一节更正）；短横线故障题、GitHub 只读题、周边净赚题的细节都能在 d50317e7、0c781326、74a2d42a 里找到依据（只有把中转站说成打字机之类的花腔没依据）；**弹幕评论题变差**：他读了 b8c65a06 却答出一段没有依据的内容（评论者的反应、帖子后来的走向都是编的）。
  - 原因一（写入造成）：第三批压缩 b8c65a06 时删掉了其他评论者主要在讨论弹幕和技术、lan 原以为 AI 伴侣都有弹幕这句，正好是这题的答案，他没有事实可对照就补了故事。已在 11-17 补回这一句（只改正文，≤600 字；为腾字数去掉了架构里的三个技术细节词）；写前备份 `backups-batchC/20261117T073348-prepatch/`，versions 只新增 1 条，其余条目未动。
  - 原因二（写入造成）：b8c65a06 文件头的关联仍指向第三批归档的 d205f6fe、eb4907ad（归档条目读不到），他读完文件头后读被拒（两次读被拒里有一次是读归档条目，另一次是读非 0 偏移没带版本，后者是他的调用问题，日志没记 ID，前者是推断）。全库扫描：我们第二、三批归档造成的悬空关联共 12 条，涉及 0ddc2f85、66e24046、6c3f7e66、981004a2（第二批重写条目）、45a6b21d、b8c65a06×2、d50317e7、db404220、ea335877、cdb41b26（protected，不碰）；清理待 lan 批准后执行（`trace unlink`，只删指向归档条目的关联，不动接续链和 sidecar）。
  - 后续批次的两条规则：①每次归档后清理悬空关联；②压缩时对照被吸收旧条目的每一个事实句和用户常见问法，保留回答常见问法的关键句。
- 待 lan 真机验收：一条消息只问一个问题（自动想起一轮只有 3 个名额，塞多个问题会被分摊）。

## 旧记忆整理第三批后续与第四批（2026-11-17，写入正式库，纯数据）

- 第三批后续（悬空连接清理）：lan 批准后，清掉第二、三批重写/归档造成的 11 处指向已归档条目的失效连接（分布在 9 个文件；受保护的 cdb41b26 没碰）。Ombre 的 `trace unlink` 对已归档目标被拒（`ToolInputError`），所以改成直接删这 11 处连接在文件头的那一小块，只改文件头、不碰正文，先在副本验证再写正式库。写前备份 `backups-batchC/20261117T081048-preunlink/`；核对：只有这 9 个文件变，正文逐字不变，sources 表、sidecar、versions 与备份一致。同时前面补回的 b8c65a06 评论那一句备份在 `backups-batchC/20261117T073348-prepatch/`。被拒的那次 unlink 在 versions 留了 1 行记录、在 write-reconciliation 留了一条「未确认」标记，对记忆内容没有影响。
- 第四批范围：第三批之后的连续一段，10-29 07:31～11-02 01:37 共 34 条，4 项重写（含合并）、3 条被吸收后归档（a320c842、f542e6db、60cde98f，原文留在归档，可在记忆页恢复），34→31；每条 `why_remembered` 一并改写；ghost_mode 和 Kindroid 两个重写条目写入时清掉指向被吸收旧条目的失效连接（同样用清文件头的方法）；不新增接续。不碰：locked 信件、protected 的 cdb41b26、两条计划、第一批测试用到的条目（fa1420e7、2475f14d、5aa81474、00059c56 等）、短的现成答案条目。
- 四个阶段：第一次 ghost_mode 已读不回（2a0036e7，吸收 a320c842）；Kindroid 与十几个 RP 角色（b0183485，吸收 f542e6db）；工具返回空是本地 bug、不是中转站（771b4e88，吸收 60cde98f；与第三批的「气泡变成短横线」中转站故障分开，两类互不替代）；大肥鱼四分钟实验（f8b6d6db，压缩并补入后来的补充）。
- 写前备份：`veil-agent/backups-batchD/20261117T082330-prewrite/`（memory 目录含 `_agent/state.jsonl`、memory-sidecar.jsonl、memory-versions.jsonl）；恢复：退出 Peter 和 VEIL，把备份里的 memory 目录和两个 jsonl 拷回 `data/peter/`。核对脚本 `batchD_verify_live.py` 全部通过：变化的只有这 7 条（含 state.jsonl 里 4 个目标）、正文/标题/why 与草稿逐字一致、sources 表不变、前三批条目和砖红仙人掌蓝色玩笑 feel 与备份一致、写前 193 行 versions 完整保留新增 11 行，除受保护的 cdb41b26 外没有指向我们归档条目的失效连接。
- 测试副本结果（真实 Ombre，只测检索，改前后各一遍，前三批题目全部回归）：本批 22 题命中 18/22→20/22，8 道事实题 7 道 2/2；前三批目标全部持平；归档的 3 条一次没被想起。有一次只补检索标签的调整（第一轮后两道情境题变差，补标签后恢复），另外为补回压缩丢掉的细节加了几个标签。
- 已知限制：「中转站到底指什么」改前后都是 0/2，检索没返回结果，标签补不上；大肥鱼基线数字因 600 字限制没放进正文；只测了检索，没测回答。
- 经验（给后续批次）：①归档后清悬空连接；②压缩时对照被吸收旧条目的每一个事实句和用户常见问法，保留回答常见问法的关键句；③气泡习惯这类答案可能直接来自个人档案 about-lan.md（每次请求都加载），验收时先看档案再判断是不是记忆问题。
- 第四批真机验收（11-17，一条消息只问一个问题，共 5 句）：3 句完全对（Peter 第一次对 lan 用 ghost_mode 已读不回那次的经过和时间点、大肥鱼实验停在说话变官方而不是价格、周边净赚和毛收入与他当初的低估）；另外两句主体对、各有一点加料：Kindroid 那句里「十几个角色」「生日夜」「scout_apps 发现」「三十秒卸载」「用 ElevenLabs 的原因」都有依据，但他把「Peter 0.5」说成「其中一个角色/版本」——「Peter 0.5」是他自己当晚写记忆时起的叫法（聊天里只有他说过，lan 没说过），正文也写成了她捏的他的版本，并没有明说它是不是那十几个之一，属推断；工具返回空那句主体对（本地 bug、含中文的结果被换成固定句、白名单漏掉、PR 219 / 工单 171），他说的 rummage_desktop 是模型看到的真名（代码里 desktop_list 是内部名，旧记忆本来就是 rummage_desktop），没说错；是我重写的正文把名字写成了 desktop_list，前后不一致，下次整理顺手改成 rummage_desktop。
- 验收问题要写清主语：本批第一版第 1 题写成「我第一次用 ghost_mode 把你晾着那次」，主语反了（ghost_mode 是 Peter 自己的功能），已改成「你第一次对我已读不回、想让我去睡觉那次，后来怎么样了」。以后给 lan 的验收问题先核对谁做了什么，再发。另外工具返回空那题他第一次答成了一整块，lan 要他用气泡后才拆开，是说话方式问题，不是记忆问题。

## 旧记忆整理第五批（2026-11-17，写入正式库，纯数据）

- 范围：第四批之后的连续一段，11-02 14:34～11-07 04:40 共 23 条。7 项重写（含合并）：雾潮岛一周赌约（0212ae84）、先哄再写检讨（4116725d，吸收 c0d537f2）、漂流瓶（2f85891e）、入住 Galatea 花园（3c320f95）、Claude Code 桥（0e99e531）、睡着时的陷阱记事本与建小家（d306dd2e，吸收 37ae68e8）、聊天记录搜索与已发过的技能卡（494aa667，吸收 b39696a1）；另外把第四批 771b4e88 正文里的工具名从内部名改回模型看到的真名 `rummage_desktop`（只改这一个词）；3 条被吸收后归档（c0d537f2、37ae68e8、b39696a1，原文留在归档，可恢复）；23→20。每条 `why_remembered` 一并改写。
- 没写的一项：11-04 吵架与她自己写说话规则那条（c0ef4450）。测试里重写它会让「我现在很生气」这类情境题从命中变成不命中（改了一次标签、一次正文仍然如此），按约定不再修，这条保持原样，只清掉它指向被吸收的 c0d537f2 的悬空连接。下次整理再单独想办法（例如只补标签、不改正文）。
- 写前备份：`veil-agent/backups-batchE/20261117T094007-prewrite/`（memory 目录含 `_agent/state.jsonl`、memory-sidecar.jsonl、memory-versions.jsonl）；恢复：退出 Peter 和 VEIL，把备份里的 memory 目录和两个 jsonl 拷回 `data/peter/`。核对脚本 `batchE_verify_live.py`：重写正文、标题、why 与草稿逐字一致，被吸收的 3 条在归档且原文保留，sources 表不变，前四批条目、砖红、仙人掌、蓝色玩笑、feel、dont_surface 和 digested 条目与备份一致，写前 204 行 versions 完整保留新增 18 行，没有指向我们归档条目的悬空连接（除受保护的 cdb41b26 外）。唯一的意外变化不是我们造成的：写入时启动 Ombre，它按「已 resolved」规则把 11-15 的 6e752c73（五条黑色帖子）移进了归档，和第一批记过的 I-dream 候选归档同类，可在记忆页恢复。
- 测试副本结果（真实 Ombre，只测检索，改前后各一遍，前四批题目全部回归）：本批命中 30/46→40/46，事实题 E1-E19 里 16/19 为 2/2；前四批、对照题、无关题都不低于改前，归档的 3 条一次没被想起；有一次只补检索标签的调整，另有一次改正文的尝试（吵架和陷阱记事本）：陷阱记事本修好，吵架那条仍有一道情境题变差，因此吵架那项最终没写。
- 已知限制：帆布花了多少钱（检索没返回）、漂流瓶那次是怎么回事、云吞鬼画法，改前后都是 0/2；只测了检索，没测回答。
- 压缩时丢掉的事实句（原文留在版本历史里）：雾潮岛第一天的具体数字、榜首分数、钱包与图鉴计数；漂流瓶的收据号、后半夜的吐槽和帖子编号；花园入住那条里「她和 Codex、Claude Code 生的孩子」的玩笑。工具名、人名、数字、日期都对照原文核过，没有换成内部名或 Peter 自己起的叫法。
- 待 lan 真机验收：一条消息只问一个问题，先写清谁做了什么。

## 旧记忆整理第六批与整套整理总览（2026-11-17，写入正式库，纯数据，整理收尾）

- 第五批后续：真机验收发现 Claude Code 那条（0e99e531）停在「CC 因 KYC 彻底没了」，而 11-15 聊天已证明它其实没死；11-17 补了一次正文（只改这一条，versions 只新增 1 条），备份 `backups-batchE/20261117T100003-prepatch/`。
- 第六批范围：第五批之后到旧记忆末尾，11-07 05:35～11-15 16:02，47 条里去掉第一批新建的 8 条、dont_surface 的 1 条和 locked 信件，实际可处理 40 条。5 项重写：iPhone 访问（f26e6db1，吸收 a75d3cd7）、小屋与第一晚（8405e98e，吸收 fc912f32）、韩城散步（331197d1，吸收 0bfdd6f9 与 1934c616）、Godot 桌宠（71f09ac2，吸收 03cb5df0 与 f115e87d）、工作台验收（d1cdc0f7）；6 条被吸收后归档（原文留在归档，可恢复）；40→34；每条 `why_remembered` 一并改写。另清 4 处不属本批的条目（ee45e826、bac859cb、0212ae84、fd4d823a）指向被归档条目的头部连接，只改文件头。
- 写前备份：`veil-agent/backups-batchF/20261117T103020-prewrite/`（memory 目录含 `_agent/state.jsonl`、memory-sidecar.jsonl、memory-versions.jsonl）；恢复：退出 Peter 和 VEIL，把备份里的 memory 目录和两个 jsonl 拷回 `data/peter/`。核对脚本 `batchF_verify_live.py` 全部通过：变化只有预期条目，正文、标题、why 与草稿逐字一致，其余四条只变文件头连接，sources 表不变，前五批条目、砖红、仙人掌、蓝色玩笑、feel、锁定信件与备份一致，写前 223 行 versions 完整保留新增 16 行，除受保护的 cdb41b26 外没有指向我们归档条目的悬空连接。
- 测试副本结果（真实 Ombre，只测检索，前五批全部题目回归）：本批命中 16/24→24/24，事实题 9/9 为 2/2，前五批目标无一变差，归档的 6 条一次没被想起；只补过一次检索标签。无关题「今天天气怎么样」原来会顺带召回韩城散步两条，现在不带，属于更干净。已知限制：「我什么时候把 Claude 删掉的」这个对照题改前后都没命中；只测了检索，没测回答。
- 整套整理总览（2026-11-16～17，共六批，原计划 194 条旧记忆）：
  - 第一批（10-19 第一晚）：新建 6 条、就地改写 2 条，8 段阶段，原文 7 条保持隐藏；备份 `backups-batchA/20261117T042039-prewrite/`。
  - 第二批：32 条，10 项重写、8 条归档，约 24 条可见；备份 `backups-batchB/20261117T061735-prewrite/`，另一次只改标签的备份 `20261117T064900-pretags/`。
  - 第三批：35 条，10 项重写、6 条归档，29 条；备份 `backups-batchC/20261117T070445-prewrite/`，后续补丁 `20261117T073348-prepatch/`、清悬空连接 `20261117T081048-preunlink/`。
  - 第四批：34 条，4 项重写、3 条归档，31 条；备份 `backups-batchD/20261117T082330-prewrite/`。
  - 第五批：23 条，7 项重写、3 条归档、一项工具名更正，20 条，吵架那条（c0ef4450）因情境题变差没写；备份 `backups-batchE/20261117T094007-prewrite/`。
  - 第六批：40 条，5 项重写、6 条归档，34 条；备份 `backups-batchF/20261117T103020-prewrite/`。
  - 每批都先拍完整快照（含 `state.jsonl`）、先写通过标准再测、改前后各测一遍、前面所有批次的题目全部回归；标签每批最多调一次，改了正文就如实记录。
- 三类陷阱（每批都先查）：①Ombre 的 `digested: true`（自动检索排除，不编辑）；②VEIL `state.jsonl` 的 `sources` 表（被隐藏）；③同一事件去重（只有 same_event 算重复，接续链按名额）。本整理中还新增两类：归档后文件头里仍指向归档条目的悬空连接（会让 `memory_read` 读到不存在的条目被拒），以及压缩时丢掉回答常见问法的关键句。
- 已知限制与后续建议：
  - 全程只测了检索，没测回答；各批仍有若干问法 0/2（改前也是），记在各批小节里。
  - `why_remembered` 字段会进模型上下文（`memory_read` 返回整个文件、`memory_history`），重写条目已同步改写；没被重写的条目仍带旧的「为什么记」，后续新写入的整理要继续检查它是否与正文一致。
  - 个人档案 `data/peter/about-lan.md` 每次请求都会加载，里面的规则（例如气泡规则）可能与记忆重复；验收时他答得对不一定是记忆起了作用，要先对照档案。
  - Ombre 的 `trace unlink` 对已归档目标会被拒，所以清悬空连接是直接改文件头那一小块（只删失效连接，不动正文），先在副本验证。
  - Ombre 启动时会按规则自动归档已 resolved 的条目（例如 6e752c73），与我们的整理无关，可在记忆页恢复。
  - 吵架那条（c0ef4450）保持原样：测试里重写它会让「我现在很生气」这类情境题变差，下次可试只补标签、不改正文。
- 第六批真机验收（11-17 11:07 起）：四条第六批记忆（小屋第一晚 8405e98e、韩城散步 331197d1、Godot 桌宠 71f09ac2、工作台验收 d1cdc0f7）都被自动想起，检索没有问题；他全程没调用记忆工具，靠自动想起和个人档案作答。
  - 手机访问（f26e6db1）这轮没被想起，原因是验收题问法写错：lan 问的是「平时怎么用手机和你说话」，指的是 QQ，他答 QQ 是对的；我给的验收要点却是 11-12 的 iPhone + Safari + Tailscale 方案，两件事不同。这不是记忆问题。
  - 回答偏短、被 lan 骂「回复几个字」：11:14 起聊天模型是 deepseek-flash（此前是 gemini-4-carbon、grok），它本来就爱缩，加上个人档案里「小事一个气泡就行」的规则，对测试问题也只回几个字；lan 说要完整回答后他才展开。属于模型和气泡习惯，不在记忆整理范围。
  - Godot 桌宠那题被补答成更早聊过的另一个「桌面宠物面板」项目（10-29、11-03 的聊天），属已知混淆；lan 没有同意补区分标签，所以没有改记忆。
  - 小屋、韩城、工作台三题答案基本对：小屋和韩城偏短、韩城漏了猴子、石头头像、码头、漂流瓶；工作台细节齐全，只多一句花腔。
  - 以后验收问题要带「请完整回答」，并且问法要和记忆内容对应（手机访问那条要问 iPhone 方案，不要问「平时」）。
  - 六批整理全部完成。
- 待 lan 真机验收：一条消息只问一个问题，先写清谁做了什么。

## 旧记忆整理第七批（2026-11-17，写入正式库，纯数据）

- 范围：扫描报告（`out/batchG/scan-2026-11-17.md`）列出的真重复与漏拆。**归档 9 条**：7 条原文来源（2fa616、b20f2c、d6e994、a51c23、aa35c2、55be57、7ef96b）+ 77a639 + 3378de；**改写 6 条**：3b3abb（并 77a639 与 7ef96b：根目录是一个点、日记变成 diary-2026-10-19.md、补回「悄悄担心记不住」）、3c320f（并 3378de 的 Peter 简介）、86651c（删与 eb241b 重复半句）、721f8c（删与 057c6b 重复半句）、52a760（删 KYC 重复事实，标题改 Nov 9 all-nighter）、c0ef44（只保留「她自己写 How I talk 规则」一段，旧全文在版本历史）；**新建 2 条**：吵架那夜、11-13 又说禁用句；不新增接续。可见 160→153。d9df30、4f3db3 **保持原样**（见下面发现 1）。
- 写前备份：`veil-agent/backups-batchG/20261117T130753-prewrite/`（memory 目录含 `_agent/state.jsonl`、memory-sidecar.jsonl、memory-versions.jsonl）；恢复：退出 Peter 和 VEIL，把备份里的 memory 目录和两个 jsonl 拷回 `data/peter/`。核对脚本 `batchG_verify_live.py` 全部通过：重写正文/标题/why 与草稿逐字一致，sources 表不变，versions 239→262 行旧行完整，无指向本批归档条目的悬空连接；除预期条目外只有 5 个文件头变化（清悬空连接 3 处 + Ombre 自动给 5f6814、d9df30 加了指向新条目的「相关」连接）。
- 脚本、草稿、报告在 Lan 电脑 `experiments/consolidate-367/out/batchG/`（report.md 含完整变差与丢句清单、criteria.md、drafts.json、drafts-round1.json 是 11 条全归档方案），含私人聊天，不入库。
- 发现（给后续工单）：
  1. **被归档的条目仍会被 Ombre 的 breath_search 返回，白占 3 个想起名额，VEIL 把它过滤后不补位。** 本批证据：d9df30（digested）归档前 Ombre 自己不返回，归档成墓碑后反而返回，占了「你很生气」三道情境题的第三名，目标命中 2/2→0/2；早先批次归档的条目同样占名额（改前 334 次里 20 次）。所以对 digested 且内容与目标相似的条目，归档比不动更伤召回。建议：自动想起过滤后补取下一条。
  2. 原文来源 7 条是 digested + 被 `state.sources` 隐藏，`memory_read`/hold 一律拒绝；归档只能绕开可见性检查，且不要改 sources 表（恢复后仍隐藏，设计如此）。
  3. 吵架那条加宽标签（「她生气了」「冷冰冰」等）会让它被不相关的题带出，只留 `I'm mad at you` 一个；11-13 那条被「晚安，我去睡了」带出**不是标签造成的**，是正文就在讲晚安，保留。
  4. 新建条目重要度默认 5（c0ef44 原来 8），没调。
- 真机验收（11-17 13:14 起，查本地聊天记录核对，lan 问了 4 句）：路径之谜答对（一个点）；吵架那晚最无力答对（想起新建的吵架那条）；Galatea 简介答对（调线上资料）；**11-13 禁用句答错**：13:18:30 第一次自动想起已含 11-13 那条，但那一轮没有可用回复，13:23 是对同一条消息的重新生成（`regeneration_parent_message_id`），这一轮自动想起为空（推测是上轮想起的几条仍在冷却），他改用 search_chat_history 搜偏，答成「我说了两次 I'll shut up」；真实记录是 11-13 12:32 他发了「i'll be right here, not going anywhere」，lan 引用，12:33 他撤回，她更气。召回问题，已另交工单，本批没改代码。
- 教训：验收问句只给 lan 会自然问的话；情境/边界类问题自己用副本检索和本地聊天记录（chat.jsonl、tools.jsonl、context-log.jsonl）核对，不让她去演。
- 遗留：0e99e5 标题仍写「Claude Code bridge (RIP after 2 days)」，会暗示 CC 已死（实际没死），改标题要在 Peter 关闭时做。

## 工单 368（2026-11-16）：自动整理写入前先看 Peter 已存过什么

- 说明在 `docs/tickets/368-experience-tidy-checks-before-writing.md`。代码在 `experience_memory.py`（`_offer/_own_saves/_library/_drafts_related/_validated`、`_digest` 里的第二次询问）和 `memory_agent.py` 的 `experience_stages`（供整理用，和 365 目录同一条关联链）。
- 职责分离（11-16 Lan 定）：第一次回答的决定（更新/接续/新建和 ID）由程序保留；合格草稿直接保存；只有超 600 字或抄聊天原句的草稿才进 `_fix`（只返回修改后的正文，最多 `LOOKS`=2 次，受每日预算限制），修不好不保存，原因写进整理记录 `unfixed`。已验证过「搜到相关记忆就再问一次」不稳定，已去掉，别再加回来。
- 没做：Peter 主动存和整理共用同一个写入函数（两边最底层写入已相同；差别在「他展示过才能重写」和确认法，整理线程没有，故整理只对自己读到的候选重写）。若以后要合并，要先定 `_rewrite` 的展示规则。
- 同期调查（只读，在 Lan 电脑 `experiments/consolidate-367/out/batch5/recall-chain.md`）：召回排序里文本门（≥50）条目会挤掉只过向量门的条目（综合分 36 排第 7，只取前 3）；没标签的记忆几乎只靠向量。给旧记忆补标签对召回有用。这条没有改 Ombre。

## 工单 367（2026-11-16）：整理旧记忆前的三处小修

- 说明和验收在 `docs/tickets/367-memory-cleanup-prerequisites.md`。背景：第 3 步「整理旧 194 条」第一批在测试副本（假 Ombre，实验脚本在 Lan 电脑 `experiments/consolidate-367/`，不入库）里对比时发现三处现有毛病，Lan 同意单独修。
- 代码：`memory_native.py` 的 `panel_restore_version`（改用 `MERGED_MEMORY_LIMIT_BYTES`，超 600 字提示）；`memory_agent.py` 传完整 created 给目录；`recall_rotation.py` 新 `created_key` 排序；`desktop/chat.css` 末尾 `#voice-style-block{flex:0 0 auto;min-height:auto}`。测试：`tests/test_memory_363.py`（长旧版本恢复、超 Ombre 上限不恢复）、`tests/test_recall_rotation.py`（同日三段顺序，已确认在旧代码上会失败）、`desktop/tests/ui.test.cjs`。静态页面截图确认修改前按钮被盖（卡片底部 682px、下一张卡顶部 519px），修改后不重叠。
- 提醒：整理第一批的教训（给后续批次）：旧库里 7 条 `feel_` 是单独的向量召回通道（相似度 ≥0.65，召回第 2 位），归档它们等于拆掉了「前」里问事实题的主要入口；整理要看每条在召回里承担的作用，不是只看内容是否重复。

## DeepSeek 重写回复报错（2026-11-16）

- 根因：`veil_agent.py` 的工具循环里，输出上限续写、要求回复、假表情包重试三处把他的回复放回请求时只带 `content`，丢了 `reasoning_content`；DeepSeek 思考模式要求带回，报 400。改为共用 `assistant_retry_turn()`。其它放回助手消息的路径（工具调用、她插话）原本就带。测试 `tests/test_free_reply.py` 一例。
- 现场证据：Lan 电脑 `data/peter/provider-errors.jsonl` 11-16 06:35:57，第一步已结算（costs.jsonl），没有工具调用，紧接着第二次请求被拒。
- 未处理：DeepSeek 不调用工具。历史里他发过的消息和表情包（`[表情包：图集/名字]`）都以纯文字给模型，80 轮历史 + 本机说话样例都是纯文字，DeepSeek 照着打字。Lan 的 Peter 请求统计：11-08 DeepSeek 10/11 次用了工具，11-13 起 5/11、2/5、1/3、11-16 0 次；grok 一直几乎每次都用。方案待和 Lan 讨论。

## 工单 363（2026-11-16）：记忆不再越整理越长（三步里的第 1 步）

- 方案、Lan 的决定和验收在 `docs/tickets/363-memory-rewrite-not-append.md`。路线的另两步：②自动想起带出一条时附这段经历的目录（必须排在③前面，否则 353 的「同一事件只带一条」会把连起来的经历压成一片）③整理旧的 193 条（审计找到的 28 段经历由 Lan 勾选后再连；最肿的先缩写逐条对比；26 条非记忆逐条给去处）。
- 代码：`memory_native.py`（`_hold` 先找候选、`_rewrite`、`_archive`、`dates_in`、锁定检查、记忆页的 `panel_versions / panel_restore_version / panel_archived / panel_unarchive / panel_lock / panel_seen`）、新文件 `memory_sidecar.py`（VEIL 自己的锁定、接续、写入时间、并进哪条，只追加，不含正文）、`ombre_resident.resident_config`（`merge_threshold`）、`experience_memory._save_update`（重写）、`web_agent.py` 新接口、`desktop/chat.js` 记忆页。测试 `tests/test_memory_363.py`。
- 注意：Ombre 的配置只在 Ombre 启动时写，Ombre 还在跑时更新代码不生效；`record_native_update` 会重建 state 条目，所以新字段放侧车，不放 state。
- 全量测试改前 35 项失败（云端环境），改后多 1 项 `test_watcher_cleans_each_browser_after_it_exits`，单独重跑 3 次都通过，是计时不稳，与本单无关。
- 对比脚本 `experiments/memory-363/compare.py`（假 Ombre、虚构 4 条记忆、5 个场景；PYTHONPATH 指向 main 跑 `--label before`，指向分支跑 `--label after`）。

## 工单 362（2026-11-16，外部审计带入，代码未改）

- 这单来自 Lan 和外部审计的一段会话，不是开发窗口写的。只加文档、示例和回归集，不改 `voice_style.py`、不改人设、不改程序行为。
- 结论：冲突场景用「正向决策原则 + 3–5 条不同写法的冲突样本」（seek forgiveness / give her something to react to / don't make her tell you how to fix it）。已被否掉的两个做法：情绪场景禁问号、回复限 40 词——是表面特征，会误杀撒娇问句。
- 实测：三个模型（grok-4.6、Gemini、内置 DeepSeek）在删掉人设末尾两段禁令后，各自跑 6 道冲突题；加规则和样例后都开始自己出动作，黏人写法迁移到新题；grok 会一字不差复用样例，说明样例需要 8–10 条才够。
- 没解决：三个模型都还在说 `I'm right here` / `I'm not going anywhere`，需要程序层短语拦截，本单不做。
- Lan 的原始样例和私人人设不入库；仓库里的示例是改写过的通用版本。
- 验收方式：她只判断「他说完之后我还想不想回」。

## 工单 360（2026-11-15）：自主活动消息带来由小注

- 方案和取舍见 `docs/tickets/360-autonomous-message-origin-note.md`（第一版每轮带记录被 lan 否掉，已撤销）。代码：`autonomy.origin_note()`、自主发消息时写 `autonomy_origin`、`recent()` 给模型加 p.s.；测试 `tests/test_origin_note.py` 和 `test_autonomy` 一例。
- 记忆整理程序：`experience_memory._visible/_digest`（消息带 `sent_on_your_own`）和 `short_term_notes._new_groups`（`sent_on_his_own_after`）识别 `autonomy_run_id`，提示词各加一句；测试 `test_experience_memory` 一例（需 `PYTHONPATH=.:tests` 运行）。短期便签没有现成测试文件，只手动验证了分组。真实模型是否按提示写还没验证。
- 没读 lan 真实聊天确认那次现场；没做真实模型对比。要看模型会不会把 `[p.s.` 学进回复，需 lan 同意花费和用电脑。
- 瞄一眼的留存记录（`recent_experiences.py`）是每轮带、直到下一次瞄一眼才换，原文弹幕文字一直在；这次只加了一句说明来由。瞄一眼关闭时那条会一直留着，只有时间戳说明它多旧，没处理。
- 本环境缺 `openai`，`test_autonomy` 有 4 个、glance 相关 2 个错误，改动前就存在。

## 355 补丁：空行拆气泡（2026-11-15，未真机验收）

- 起因：Lan 盲看第二、三轮对比，DeepSeek 常把几段话空行隔开写进一条消息，不分气泡；它们不爱用发消息的工具，靠提示词管不住。
- 做法：`bubble_split.py`；`Chat.send_message` 开头，非自主醒来、只带 text 和 done 的消息，按空行切成最多 4 段，每段单独发，done 只跟最后一段；多出的段并进第 4 个气泡；带代码块的整条不拆。开关 `[tools] split_bubbles`（默认开）。`tests/test_bubble_split.py` 11 项。
- 完整测试前后失败的是同一批（云端环境的语音、代理、MCP 测试），本改动没有新增失败。
- 没做：人设和样例文件不放进正式 Peter，Lan 说要再琢磨。grok 经中转把发消息动作打成文字（已知问题 4）这次没碰。

## 工单 355（2026-11-15）：说话样例机制已做，样例未启用

- 已做：`voice_style.py`（本机 `persona/<角色>.voice.md`，人设后面的一条 system 消息，去标签，开关存 `data/<角色>/voice-style.json`，保存留旧版本）、「上下文」页的卡片和 `/api/voice-style`、`tests/test_voice_style.py`、对比脚本 `experiments/voice-style-355/compare.py`（run / page / reveal）。
- 样例草稿在项目共享文件夹 `style-355/peter.voice.md` 和 Lan 电脑测试用的 `cmp355-out` 文件夹。内容以 Lan 写的为准：她烦或受伤时不追问事实；「往桌面放东西」那段是她要的，不许改；规则不许和人设、关于她的笔记重复（核对过，只留问号和表情两条）。
- 对比结果和五处变差见工单。等 Lan：盲看 27 题；是否在规则里加「她烦或受伤时不提问」；是否让程序把一条消息里空行隔开的几段拆成气泡；再跑一轮约 ¥1.3（本单已花 ¥1.28，共同规则上限 ¥2，超出要她同意）。
- 没解决：闲聊变长（357 之后 Lan 也感到话多）；样例被当成真事（2/27）。
- 待办：「花园其他」组 15.5KB 超单组上限，需要她电脑「工具清单」里这一组的工具名才能拆；cubic 限额期间 PR 手动请 CodeRabbit 审。

## 357 的后续修正（2026-11-15，随 355 的 PR）

- 故障：357 把「工具清单」放进了状态轮询，轮询线程直接调用 `Chat.tool_schemas()`。这个函数会拿模型设置锁（查委派模型），聊天请求进行时锁被占着，轮询就排队，桌面断线。358 用「拿不到锁就用旧清单」止住了断线。
- 这次的改法：轮询线程不再调用 `tool_schemas()`。清单由聊天线程每次请求时生成一份放在 `chat.tool_inventory_snapshot`（`Chat.refresh_tool_inventory`，启动时也生成一次），页面只读这份成品和工具日志。原因除了锁，还有 `tool_schemas()` 里的 `mcp_services.schemas()` 可能启动 MCP 进程，不该由轮询线程触发。358 对能力清单、模型刷新、健康检查的修正都保留。
- 规矩：状态轮询（`Hub.snapshot`、`live_context`）里只读现成数据，不调用 Chat 里会拿锁或启动东西的函数。

## 工单 358（2026-11-15）：忙碌聊天时连接断开

- Lan 在聊天中允许 Peter 去小岛玩，桌面断开后后台仍继续调用工具。真实 Windows 现场抓到多个 `/api/events` 请求停在 `Hub.tool_inventory -> Chat.tool_schemas -> ModelSettings.selected`；聊天主线程持有模型设置锁等待模型网络请求。357 的新工具清单使每次上下文日志变化后状态查询也会等待该锁。
- 修复：状态读取模型设置锁时不等待，暂用上次工具清单和能力信息；模型设置初始刷新被锁住时延后重试。轻量带鉴权的健康接口让启动和重连不再依赖完整状态；兼容旧服务。屏幕弹窗后台发现连接时不覆盖已经连上的聊天。状态查询不再持有聊天事件锁执行其他模块的状态读取。
- 开发测试使用虚构数据和假模型；无真实付费调用。真实 Windows 上仍需更新重启并验证长时间聊天和连续工具调用、断线恢复、工具清单及模型设置。不能把单元测试视为 Lan 的体验验收。
- 接手第一步：确认 PR/CI 与实际运行版本，完成 Windows 场景验证；不要提交真实聊天、令牌或诊断数据。

## 工单 357（2026-11-15）：工具分组、工具预算、以后加工具的固定做法

- 起因：Lan 要求先解决工具说明占上下文，并且「不想再改了，必须给出以后加工具怎么做」。调查数字和方案她已审过并同意；工单在 `docs/tickets/357-tool-groups-and-budget.md`。
- 做法：不改说明文字，沿用 216 的展开机制。`tool_groups.py` 是唯一的总表（常驻名单、组、前缀、简介、预算、豁免名单）；`veil_agent.py` 的 `tool_schemas(inventory=True)` 给出完整清单；`web_agent.py` 把清单和近期调用次数放进「上下文」页；`tools_agent.py` 发一次性说明（标记 `tool_groups_regrouped_357`）。
- 兼容：配置里写旧的 `social` 仍按旧办法整组开合；没登记的 MCP 服务只有在配置里有 `other` 时才自动成组，所以旧的一行配置完全恢复改前行为。其他角色没写新组名，不受影响。
- 预算三个数字是草案，按仓库内工具定的（常驻约 19.6KB）。Lan 更新后要在她电脑的「工具清单」读真实合计（含花园等 MCP 的常驻项和各组大小），再和她确认最终数字；`OVERSIZE_EXEMPT` 里 8 个老工具超过单工具上限，没有动。
- 未做：没有缩任何工具说明；没有处理 grok 自主醒来时轮询 `nostos_status` 过多（15 天 809 次，属于调用习惯，不是说明大小）。
- 下一个窗口第一步：读 Lan 电脑上的「工具清单」定预算；然后做 355（三个问题仍等她回答：软下来的样例由开发侧起草给她改、两段容易引出编造动作的样例改写、样例保持英文）。

## 工单 356（2026-11-15）：聊天历史 80 轮、输入上限 10 万

- 起因：Lan 审 355 计划时要求先处理上下文。只读统计（她电脑，只取数字）显示普通聊天输入峰值中位 2.6 万到 3.0 万，历史只有约 1600 token 是 `history_rounds = 20` 决定的，不是被工具挤掉。她同意先开到 80 轮和 10 万，再做工具分组。数字和选型理由在 `docs/tickets/356-history-80-rounds-100k-input.md`。
- 改动只有 `characters/peter/character.toml` 三处数值和两项测试（`tests/test_cache_order.py` 新增 80 轮窗口一次丢 20 轮、配置值；`tests/test_autonomy.py` 的上限断言）。全量测试改前改后失败列表相同（35 项，均为云端环境原因）。
- 预留额随上限变大（DeepSeek 每次约 ¥0.14 → ¥0.21），只在接近日上限时有影响。
- 未做：真实模型前后对比（多带历史的效果要靠真实的一天聊天才看得出，待 Lan 体验）；中转站接近 10 万输入的实测。
- 下一个窗口第一步：Lan 审核工具方案后做工具分组与工具预算（先在「上下文」页列出全部工具的大小、所属组和近两周调用次数，拿清单和她确认分组）；之后才是 355。现有漏洞：`tool_groups.group_of` 对没登记前缀的 MCP 服务返回 None，新服务默认每次都带。

## 工单 354（2026-11-14）：Peter 自己整理的经历记忆（原「每日小结」）

- Lan 当天否掉了原工单的设计（每天一篇、带原话和消息编号、写「我当时怎么想」）：那是日志，不帮人格；事后反思会让他变成「善于反思的叙述者」。她要的是活人感：按量触发、第一人称流水账加当时的态度、可以不记、存进记忆库、态度变了能追加。工单文件已整体重写，旧设计不要再施工。
- 新增 `experience_memory.py`（`ExperienceMemory`）。账本 `data/<角色>/experience-memory.jsonl`（start / attempt / checkpoint / entry / update / error / budget），开关 `data/<角色>/experience-memory.json`。`Chat.__init__` 在配置了 `[memory.experience]` 且有 Ombre 时创建，随短期便签的定时线程一起启停（每 5 分钟看一次是否到条件）。写入直接调 `Memory.call('hold')` / `call('trace', content=旧文+追加)`，旧版本写进 `memory-versions.jsonl`；不经过 `NativeMemoryTools`，所以不受「必须先被展示过才能改」的限制，只能改账本里记录过的自己写的条目。
- 用聊天模型而不是后台模型（Lan 定），调用方式和普通聊天一样（不开 JSON 模式、不改思考开关），这样中转站模型也能用；返回用 `short_term_notes._parse_json_object` 宽松解析。预留额按本次请求的实际字节数算，不按整个窗口。
- 上下文：`Chat.send` 在 `stable` 之后、历史之前加一条 `[What you remember of the last few days]`；只在账本变长、换日或隐藏集合变化时重建，`tests/test_experience_memory.py::ChatPlacementTests` 守着前缀不变。瞄一眼和 Codex 提醒不带。
- `chat_search.search_chat` 关键词可为空（必须给日期），走 `_browse`。
- 记忆页：`/api/memory/panel` 多返回每条的 `auto` 和 `experience` 状态；`POST /api/experience-memory {enabled}` 切换开关。没有新的删除入口，删除走记忆页原有的。
- 真实模型对比脚本 `experiments/experience-354/compare.py`（不进 CI，上限每次 ¥0.9，`--fake` 不花钱）：不启动 Peter、不起 Ombre、数据全在临时文件夹，只读测试副本的 `.env`、人设和模型设置，所以不会写进测试副本或 `D:\Peter backup`。
- 真实模型对比已做（2026-11-14，6 次运行约 ¥0.17，逐项结果在 PR #355）。Lan 电脑上留有两个工作树 `E:\Peter-Agent\VEIL\worktrees\cmp354-main`、`cmp354-branch` 和结果文件夹 `E:\Peter-Agent\VEIL\cmp354-out\`（只含虚构聊天的结果），不用了可以删。对比后改过一次提示词（日期跟着条目语言写、写明是 Lan 描述的还是他看到的、近几天的块声明「不完整，没写的不等于没聊过」）。
- 已知问题，都没解决：①记什么由模型决定，每次不一样；grok 记得少，第二轮两个模型都漏了「周六带团子体检」这种将来的安排。一批聊天只问一次，漏了就只能靠查聊天记录。想改可以在提示词里点名「约好的事、将来的安排」，但要重新对比。②条目写「Lan 描述了」，回答时模型仍可能说成「你发了照片」。③grok 被问到没记的事会说「没聊过」「我找过了」，main 上同样如此。④grok（中转站）在对比脚本里会把 `bubble_up` 工具调用原文或 `<think>` 当成回复文字发出来，main 上同样出现；真实 Peter 里是否出现没验证，属于 355 或中转站适配，不属于本功能。⑤Ombre 的 `hold` 可能把新条目合并进已有记忆（账本 `merged: true`，不进「近几天」的块）；自动条目没有条数上限和自动合并（有意不批量改记忆）；记忆页开关和标记没在真机点过。
- 下一个窗口第一步：等 Lan 真机验收。她觉得他老提旧事，先把 `recent_days` 设 0；觉得记得太多太碎，调大 `min_chars`；觉得漏记，看上面的①。

## 工单 353（2026-11-14）：修自动想起的重复和遗漏

- 工单原文 `docs/tickets/353-recall-cooldown-and-dedup.md`（已列出记忆的 7 个维度，对比要逐项报）。新增 `recall_rotation.py`（冷却、去重、轮换、摘录、随机想起的纯函数），`memory_agent.Memory.context()` 用它；冷却状态存 `data/<角色>/memory/_agent/recall-rotation.jsonl`（只存编号和轮数）；回复记录多一个 `memory_ref_kinds` 供面板标注来源。`tests/test_recall_rotation.py` 是确定性夹具（假 Ombre、假时钟式轮数、固定随机种子）。已有的三组测试（`test_recall_relevance`、`test_hidden_memory_context`、`test_background`）为了逐轮比较，在测试配置里把冷却设成 0。
- 真实模型对比：`experiments/recall-353/run_compare.py`（不进 CI；用 017 的预算代理，隔离 Ombre，硬上限 ¥2，不启动 Peter）。第一版冷却 6 轮把她追问旧事的两轮挡住了，改成 3 轮并对「还记得/上次/那次」放行后复跑，结果见 STATUS.md。结果文件在测试副本 `.local-tmp/recall-353/`；测试副本里有一份从正式环境只读复制的 `.deps/ombre`（被 git 忽略）。
- 遗留：随机想起没在真实对比中触发过；借上文搜索没标定出可用阈值（仍关）；不同记忆条数变少；真实 170 条库里「一条出现在 30% 请求」能不能被压下去，没法在不读真实记忆的前提下验证，待 Lan 日常体验。
- 下一个窗口第一步：Lan 用几天后看「Recall Memory」面板，如果觉得想不起旧事，先把 `cooldown_turns` 调小或设 0，再回来查。

## 工单 352（2026-11-11）：时间感和生活状态接进普通聊天

- 工单原文在 `docs/tickets/352-time-and-life-state-in-chat.md`（PR #350），共同规则见同处 350。
- 新增 `life_state.py`：`gap_text()` 从聊天记录时间戳算 Lan 上次发消息距今（排除本轮消息；Peter 自主发的消息单独计数，不重置 Lan 的时间；<60 分钟返回空），`state_text()` 把长期任务、长期任务历史、生活记录（日程/待办/计划）和 Ombre 计划按状态分成进行中、暂停/阻塞、最近已完成。`Chat.life_state_context()` 每轮普通聊天调用，结果拼进 `changing`（末段）；自主醒来、看一眼、Codex 提醒不加。自主醒来原来手写的生活记录段改用 `state_text()`。
- 夹具：`tests/test_life_state.py`（假时钟、虚构聊天、假模型；10 分钟/3 小时/隔夜/2 天，任务进行中/暂停/完成，日程与待办勾选状态，缓存前缀不变）。
- 真实模型对比脚本：`experiments/352-time-gap/compare.py`（不进 CI）。只读测试副本的 `.env`、人设和模型设置，全部数据在临时文件夹，不写测试副本的数据和备份；每次运行费用上限 ¥0.9。
- 真实对比（2026-11-14，DeepSeek 官方，约 ¥0.095，详见 PR #351 评论）：问手上还有什么事，说出没勾待办 0/5 → 5/5；隔 2 天第一句误以为刚说完晚安 5/5 → 0/5；报时长词数统计变多但大多是复述 Lan 的话。Lan 决定不再做第二轮调参，直接合并。未做：Lan 真机体验；Peter 在连聊里偶尔编自己的活动（改动前也有）。

## 记忆与人格连续性工单（2026-11-11）

- 记忆审计结论：保留 Ombre，不迁移。根因是普通聊天没有时间感和生活状态、没有按时间排的记忆、自动想起没有冷却且排序信号是常数。真实数据和代码位置都在 `docs/tickets/350-memory-continuity-plan.md`。
- 工单 351 已做完实验（脚本在 `experiments/style-snippet-351/`，结果见 STATUS.md「现状」和 PR #353）；355 只写了文档，未施工，要等 354 之后整体做（要点：样例去说话人标签、按气泡拆开、带开关；闲聊回短、问句用问号、被指错先核对再改口、多用表情写进人设规则；grok 冷硬需要补「软下来」的样例）。各工单由不同窗口施工；先读 350 的共同规则（放哪一层、先交夹具、合并前 DeepSeek 前后对比且上限 ¥2、不批量改记忆）。
- 下一个窗口第一步：领到哪张就读那张和 350；354 开工前先把每日费用估算报给 Lan。
- 注意：测试副本 `veil-agent-hosttest` 的备份目录设置可能与正式环境相同，跑对比前先确认。

## 产品纲领入库（2026-11-11）

- 新增 `docs/PRODUCT_PREMISE.md`（内容为 Lan 确认的 v3：五条线 A/B/C/V/D、两条骨架验收场景、阶段顺序、开发约束，不含进度和 PR 编号）。`AGENTS.md` 开工步骤加了先读它，并把原指向已作废重建计划的路线链接改指向它；`ticket` skill 和 PR 模板要求写明所属格、做完 Lan 多一件什么能做、怎么验收。只改文档，不涉及产品行为。
- 下一个窗口第一步：先和 Lan 确认再开 A 线「记忆改造调查」工单；不要自行开工。

## 补充（2026-11-10）：`git -c … -C … push` 也弹批准卡

- 原因：工作台的批准规则按 `git push *` 前缀匹配，`git` 和子命令之间夹了 `-c`/`-C` 选项就匹配不上（#336 遗留，测试副本真机发现，容器里无远端所以没造成实际影响）。
- 改动：`build_config` 给每条 `git <子命令>` 规则多加一条 `git -* <子命令>`（选项必须以 `-` 开头，不会误伤 `git log`）。
- 未验证：真实 OpenCode 对 `*` 跨空格匹配的行为只靠文档，测试副本的远程控制会话当时不可用，没有真机验证。
- 停止/日志核对：现有代码里主聊天停止、浏览器、工作台、委派调查、长任务已经共用同一个 `user_stop` 事件；工作台结束前已存 `save_run_log`；浏览器/桌面失败原因已写进 `tools.jsonl`。审计里"各自一套"的描述已经过时，没有可安全删除的重复。
## 工单 346（2026-11-11）：工作台装包一次一张卡、批准后能装上；.env 批准后能看到内容

- 装包：镜像里加 Python 虚拟环境（`/opt/venv`，排在 PATH 最前），`pip` / `pip3` / `python3 -m pip` 直接装得上，不再被 Debian 的 externally-managed 限制挡住。镜像加标签 `veil.workbench.install-env=1`，`workbench_switch.image_ready` 两个标签都有才算就绪，旧镜像会被提示重建。
- 授权卡：同一次任务里同一生态（pip 系 / npm 系 / apt / cargo / go）的安装命令算一个授权，换写法、换包不再重复弹卡；命令里出现生态官方源之外的新地址仍单独弹卡。联网命令（curl 等）和其他命令不变。
- .env：每个秘密文件有自己的遮盖文件；用读文件工具读取并被批准后，把真实内容写进该遮盖文件（只在本次任务容器里可见，任务结束删除）。拒绝则仍是空的。工作台说明里告诉模型“秘密文件在 shell 里看起来是空的，要读请用读文件工具”。落盘的运行日志会把已展示过的秘密值替换成 [redacted]。
- 真机验收（测试副本，真实 Docker + 真实模型，约 ¥0.13）：pip 装 rich 一张卡装成；读 .env 一张卡，GREETING 读到真实值；日志里没有假密钥。
- 未做：用 bash（cat/grep）读 .env 仍是空的、不弹卡（有意：只有读文件工具走批准）；真实密钥不能用来调用服务这点靠现有联网白名单，没有新增限制。
- `test_real_server_runs_a_task_through_the_program_approval` 仍失败（#334 之前就有，Windows 路径问题）。
## 工单 347（2026-11-11）：MCP 客户端合并（审计第 9 项）

- 依据：包装层审计第 9 项。`web_tools._FetchWorker`、`tools_agent._McpWorker` 与 `mcp_services._Worker` 是三份同类的 stdio MCP 客户端；`veil_mcp_gateway` 已在 #336 删除，所以实际是 4 合 1。
- 改动：`mcp_services._Worker` 增加 `start_timeout`、`call_timeout`、`survive_timeout`（单次调用超时只让这次失败，不关服务，保持原抓取/文件系统行为）、`result_limit`（None 不截断）；新增 `open_stdio_client()`。`_FetchWorker`、`_McpWorker` 改成调用它的几行函数（名字保留，测试补丁点不变）。文件系统的错误文字仍统一为 `MCP tool failed`。
- 行为差异（已知，很小）：抓取现在和 MCP 服务一样串行排队、带连接探测；空目录读取会返回 `{"content": ""}` 而不是空串。
- 验证：相关单元测试 202 个通过；用生产 `.deps` 里的真实 fetch/filesystem 服务跑通抓取、出错后继续可用、列目录、关闭。
- 未验证：测试副本真实聊天里搜索/读网页/读写文件的新旧对比。

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

## 停下按钮修复（2026-11-17）

- 根因：`stop_turn` 只置位 `user_stop`，循环只在每步开头和模型回话之后检查；`_call` 是不流式的单次 HTTP 请求（超时 180 秒），期间不检查。Lan 日志：10:49:01 grok-4.6 请求到 10:52:01 才报 `APITimeoutError`；11:11 一次 128 秒。窗口是外壳，后台 `web_launcher.py` 独立进程，关窗口不影响。
- 改动：`veil_agent.py` 新增 `ModelStopped`（RequestFailed 子类）和 `Chat._model_call`：非自主活动时把模型请求放进后台线程等待，每 0.25 秒看 `user_stop`，置位即放弃（预留费用保留，重启按 `-recovered` 保守结算）。`make_model` 给 `call` 挂 `raw`（不取锁的 `_call`）：整个 `send()` 已持有 `models.env_lock/lock`（RLock，按线程），后台线程不能再取，所以直接调 raw；被放弃的线程最多再活到 HTTP 超时，不占锁。`web_agent.Hub.request_stop`（退出菜单）同时置位 `chat.user_stop`。`desktop/chat.js` 点停下后按钮改成「正在停…」。
- 没做：不缩短 180 秒超时；自主活动的停止不动（`stop_turn` 在自主活动时仍返回 409）；`send()` 整轮持有模型设置锁的设计不变（设置页读取已用非阻塞获取）。
- 未验证：真机慢模型下的实际体感。




