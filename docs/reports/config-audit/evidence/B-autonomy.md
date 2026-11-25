# 域 2 取证：自主活动与后台运行（VEIL 全局配置审计）

> 取证对象：main-src 快照（提交说明为 main e7e7992；快照 HEAD 1263a2e）。只读审计，未改任何源码/数据。
> 快照不含 data/（.gitignore:10），且本次不允许读正式运行目录，因此运行中的开关与 json 状态无法读取；涉及"当前实际值"一律标注代码默认值与仓库 characters/peter/character.toml，不臆测 data。

---

## 1. 执行链

### 1.1 [autonomy] 表：读取 → 校验 → 使用

1. 服务启动时 load_character() 读 characters/<角色>/character.toml（veil_agent.py:832-838），只在启动调用一次（web_agent.py:3703；CLI 路径 veil_agent.py:6890），无热加载。
2. 取 [autonomy] 表逐键校验（veil_agent.py:1016-1041）：
   - interval_minutes = positive_int(...默认 180)（1017）；positive_int 要求 type 为 int 且 >0，bool/字符串/0 抛错（728-731）。
   - daily_max_runs（1018，默认 6）、min_alarm_gap_minutes（1019，默认 15）同样 positive_int。
   - daily_budget_cny = price(...默认 '2')（1020）；price 要求有限且 >0（754-758），所以不能配 0 来关闭。
   - quiet_hours = parse_quiet_hours(...默认 '')（1021）；空串= None；格式必须 HH:MM-HH:MM，时 0-23、分 0-59，首尾不得相同（734-751）；跨夜（end<start）合法。
   - quiet_hours_enabled（1022）默认 = quiet_hours is not None；非 bool 抛错（1023-1024）。
   - threads（1025）默认 True；非 bool 抛错（1026-1027）。
   - max_input_tokens（1028）可选，存在时才 positive_int（1036-1037）。
   - run_budget_cny（1029）可选 price（1039-1040）；max_tool_steps 仅在 run_budget_cny 存在时才解析，否则留给 [tools].max_steps（1039-1041）。
3. 归一化后的字典写入 Character.autonomy（1031-1041, 1156-1160）。
4. 真正使用点：AutonomyController.__init__ 把 interval/daily_max_runs/daily_budget/min_alarm_gap/quiet_* 固化为实例字段（autonomy.py:441-447）；GlanceController 读 cfg.glance（autonomy.py:1454-1461）；Chat._send_with_input_limit 读 cfg.autonomy['max_input_tokens']（veil_agent.py:4016）；Chat._tool_chat 读 run_budget_cny / max_tool_steps（5583-5593, 5635-5642）；run 结果文案读 run_budget/max_tool_steps（1347-1356）。
5. 缺省/非法/耗尽：
   - 非法值 → ValueError → 统一 UserError「角色配置无法读取或字段无效，请检查 character.toml。」（1173-1174）→ 启动失败并写 web_service_error.txt（web_agent.py:3787-3794）。改坏等于起不来，不是回落默认。
   - 缺省 → 用代码默认值（不写 [autonomy] 等价 interval 180 / 6 次 / ¥2 / 无安静时段 / threads=True / 无单次花费上限）。
   - 耗尽：每日次数满 → _blocked_by_lan 直接 true，到期闹钟标 missed（autonomy.py:919-923）；日预算满 → BudgetExceeded「自主活动已达到 ¥X/日上限，聊天不受影响。」（veil_agent.py:1448-1457）；单次花费满 → 本轮记「搁置」「达到每次 ¥{cap} 的花费上限。」（autonomy.py:1347-1350）；15 分钟硬时限 → 「失败·达到每次 15 分钟的硬时限。」（1338-1340）。

### 1.2 自主活动启停

ActivityState（veil_agent.py:1177-1217）→ 文件 data/<角色>/resident-state.json（web_agent.py:3728；veil_agent.py:6632 同路径）。读：缺文件=关（1189-1190）；坏 JSON=关（1191-1192）。写：临时文件 + fsync + os.replace，内容仅 {"enabled": bool, "enabled_at": ISO|null}（1205-1217），enabled_at 只在 false→true 时刷新（1212），是自然唤醒计时基准（autonomy.py:732-734）。
页面 /api/activity（web_agent.py:2792-2803, 3421-3422）→ Hub.set_activity（1468-1478）→ chat.set_activity_enabled（veil_agent.py:2708-2721）→ 写 resident-state.json 并写能力记录；能力记录失败会回滚开关（2718-2720）。生效：should_yield() 每步检查 activity.enabled()（autonomy.py:1276-1279），暂停在下一个工具步骤边界生效，不打断在途模型请求。恢复后自然唤醒从 max(enabled_at, 上次记录, 节奏起点) 重算（autonomy.py:731-758）。

### 1.3 安静时段

AutonomyController.quiet_enabled()（autonomy.py:836-847）：没配 quiet_hours 恒 False；否则读 data/<角色>/autonomy-quiet.json {"enabled": bool}，缺文件回落 quiet_hours_enabled（842；446），坏文件抛 UserError 停止调度。_quiet_bounds 用当日/前一日双窗口判断跨夜（872-883）；in_quiet 供 due/claim（885-888, 917-934）。页面 /api/quiet-hours（web_agent.py:3138-3149）→ set_quiet_enabled（849-864）写 json 并记能力记录，失败回滚。
效果：安静时段内不发起自然唤醒（下次时间推到窗口结束，autonomy.py:767-770）；到期闹钟落入时段 → 标 missed 不补响（924-933）；set_next_wake 拒绝把闹钟定在时段内（601-603）。

### 1.4 自然节奏 / 闹钟 / 唤醒

- 节奏文件 data/<角色>/autonomy-rhythm.json（autonomy.py:440）。_read_rhythm 校验 mode ∈ normal/quieter/silent/custom（26），custom 另需 30-360 的 int（643）；过期或缺失=normal（627-650）。
- set_wake_rhythm（652-686）：mode 必填；minutes 15-4320；reason 1-300 字；custom 另需 interval_minutes 30-360。normal 分支实际写一个「quieter 到今天」的记录来提前结束（657-666）。
- _mean_interval：normal=interval，quieter=×2.5（27），silent=None，custom=自定义（688-697）；_natural_wait 用 Gamma(3,1/3) 阈值 + 对数正态漂移，下限 min(20, mean/3)（712-729）。
- 闹钟文件 data/<角色>/autonomy-alarm.json（439）。上限：pending ≤12（25, 606-608）、原因 ≤300 字（22）、最多提前 7 天（21, 592-600）、最早 min_alarm_gap_minutes（591, 594-598 自动顺延而非拒绝）、历史保留 10 条（24, 528）。状态 pending/consumed/missed/cancelled（23）。
- claim_due 先消费再启动，崩溃也不重放（949-969）；重启时 _recover_active 把未完成的 autonomy-active.json 投影成「服务异常停止，本次不会自动重试。」记录（783-834）。

### 1.5 单次运行上限与预算

- AutonomyController.run 设 deadline = monotonic + 15 分钟（1281-1288；常量 17），should_yield 每步比较（1276-1279）。
- 上下文：_context() 拼装后 >32KB 时按 reads→earlier→life→lab→travel→thoughts→threads→previous 顺序逐块删除（1228-1247；常量 18）；聊天交接卡压缩到 1500B（131-137；常量 19）。
- 预算：Budget.autonomous() 把线程标记 category=autonomy（veil_agent.py:1371-1381）；reserve() 先查分类日上限再查全局日/月上限（1537-1563），所以自主花费同时计入全局日与月预算。分类上限在 _enforce_category_budget（1448-1457）。启动时 budget.autonomy_daily = cfg.autonomy['daily_budget_cny']（web_agent.py:3710）。
- 工具步数：configured_steps = [tools].max_steps，有 run_budget 则改用 [autonomy].max_tool_steps（5583-5589）；autonomy 循环多给 1 步收尾（5593）。到限写「Autonomous activity reached the N-tool-step limit for this run.」（6148-6151, 6213-6216）。
- 输入窗口：autonomy 若在 models.json 单列模型，用该 provider 的 max_input_tokens 与 [autonomy].max_input_tokens 取小（3992-4026）；未单列只用 [autonomy].max_input_tokens（4016）。

### 1.6 Glance（瞄一眼）

GlanceController.__init__（autonomy.py:1451-1479）：平台必须 win32、cfg.glance 非空且 enabled=true（1455-1456）；阈值 15/40/300/4/1（1457-1461）；开关文件 data/<角色>/glance-settings.json（1463），缺文件默认 True（1481-1492，前提 platform_enabled）。due()：桌面解锁且空闲 < idle_seconds（1516-1536）、活动开关开（1517）、分类预算未满（1538-1556，30 秒缓存）；符合条件时在 [min,max] 随机取分钟数排下一次（1558-1572）。run() 设 glance_mode、glance_tool_limit=max_steps、走 budget.glancing()（1626-1661），≤4 步（1460；veil_agent.py:5589）。上次结果写 glance-last.json，聊天交接写 glance-delivered.json（1684-1688；407-429）。

### 1.7 Codex 监视

CodexWatchController（codex_watch.py:61-92）：开关文件 data/<角色>/codex-watch-settings.json，缺文件默认 True（111-119）；状态文件 codex-watch-state.json。claim_due() 每 10 秒扫一次（16, 356-373），读 %USERPROFILE%/.codex/sessions 下最新 rollout jsonl（72-73, 169-188）；单次扫描 8MB、单行 8MB（18-19），记住最近 500 个事件 key（20, 199）。只有 task_complete/turn_aborted 或 10 分钟无新记录（STALE_SECONDS 17, 338-347）才提醒。提醒走 glance 模型与 glance 日预算（417-419；veil_agent.py:3996 role=glance）；投递先试 screen_pop popup（455-465），否则 QQ（468-474），都没有则失败。不受 activity 开关约束（356-359 只查自身 _enabled）。

### 1.8 后台整理 / 便签 / checkpoint

- 短期便签：[short_term_notes].enabled 默认 False（veil_agent.py:1000-1007），true 才构造（2370-2375）；线程每 30 分钟跑（short_term_notes.py:13, 134-145），要求已有完整问答组（326-349）；上下文最多 10 条/800 字（15-16, 238-250）；单条 ≤240 字（17, 96）；单次输出 512 tokens、请求 ≤20000B 或 provider 一半（19-20, 540）；便签 60 分钟后过期（14, 205-227）。
- 经历自动整理：[memory.experience] 经 load_settings 严格校验（experience_memory.py:84-104）：未知键直接报错；enabled 必须 bool；day_end 必须 %H:%M；daily_budget_cny ≥0（与 autonomy 的 >0 不同）。due()：字数 ≥min_chars 且空闲 ≥idle_minutes → volume；否则需 ≥floor_chars 且过 day_end（或跨日）且空闲 ≥day_end_idle_minutes（287-304）。线程 300 秒一跳（36, 160-166）；失败退避 30 分钟 → 2 小时 → 次日（49-50, 306-320）。单次请求可见文本 ≤12000 字（43, 371-378）；输出 8000 tokens（44, 545）；日花费上限在 reserve 前预检（549, 610）。
- 长期任务：progress ≤40 条、tool_events ≤12（long_tasks.py:18-19, 339-367）；超过 3 天未更新不进唤醒上下文但仍保存（autonomy.py:148-150, 1176-1180）。
- 调查 checkpoint：证据 ≤16 条、只读证据 15 分钟内视为新鲜、恢复时阻止重放（investigation_checkpoint.py:14-16, 103-121, 156）。
- 普通聊天 work checkpoint：开关 [context].work_checkpoint（veil_agent.py:1152-1155；默认 True，非 bool 报错）；只投影真的改过东西的回合（work_checkpoint.py:86-124），48 小时未更新即不注入（19, 171-179），命中/未确认/失败保留 6/4/3 条（15-17）。

### 1.9 其他定时触发（同域）

- reply_later：[tools] 下 reply_later_max_hours/max_deferrals/max_pending/daily_wakes 默认 24/2/3/20（veil_agent.py:922-925）。实现 3148-3212；claim_due_reply_later 每日最多 20 次唤醒（3280-3282），到期落在安静时段顺延到结束（3290-3294）。hub 每轮空闲轮询（web_agent.py:1394-1398）。
- 日历：hub 每 5 秒轮询（web_agent.py:1378-1379）；每日待办提醒只在 23:30–06:00 且 Lan 空闲 ≥20 分钟时领取（1381-1382；life_records.py:296-319）；23:50–06:00 另有「顺口提一句」（321-341）；事件提醒迟到超 3600 秒记 missed（254-258）。
- 聊天约定自动记日历：仅 life-records 的 auto_record 开启时（calendar_agreements.py:29-33, 85），命中时间词/同意/取消才跑一次小模型（512 tokens，thinking disabled，70-74），窗口今天~+60 天（60-62），24 小时内可撤销（380）。
- 外部通道：邮件 poll_seconds/daily_max_sent（character.toml 192-193；email_channel.py:91-92, 130-132, 185-186），QQ poll_seconds/idle_seconds/daily_max_sent（toml 199-202；qq_channel.py:206-208, 236-253, 292-293）；AwayBridge 发送队列每 2 秒唤醒、接收按 poll_seconds 等待（away_bridge.py:152-155, 194-201）。通道对象只在启动时创建（web_agent.py:3725-3727）。

---

## 2. 配置清单表

> 源码位置=读取/校验代码；存储=落盘文件；「仅改文件」=无页面入口；「代码写死」=无入口且不建议开放。
> 当前 toml 值取自 characters/peter/character.toml（括号内行号）。

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| autonomy | interval_minutes | veil_agent.py:1017（positive_int 728-731） | character.toml [autonomy] 整数；当前 90（139） | 180 | 正整数分钟，无上限校验 | 仅改文件 | 无覆盖；节奏 quieter/custom 可临时改写平均间隔（autonomy.py:688-697） | 服务重启 | 随仓库文件 | 太小可 1 分钟醒一次，只靠 daily_max_runs 拦；建议加下限 30 | A |
| autonomy | daily_max_runs | veil_agent.py:1018 | toml 整数；当前 12（140） | 6 | 正整数次/本地日 | 仅改文件（页面只读显示 已醒 x/y 次） | 硬上限，压过闹钟：用满后到期闹钟标 missed（autonomy.py:919-923） | 服务重启 | 随仓库文件 | 程序安全上限；过大放大费用与打扰 | P |
| autonomy | daily_budget_cny | veil_agent.py:1020（price 754-758） | toml 字符串金额；当前 "3"（141） | "2" | 有限且 >0 的十进制人民币/日；0 非法 | 仅改文件 | 分类日上限；同时受全局日/月预算约束（veil_agent.py:1537-1553） | 服务重启（Budget.autonomy_daily 启动赋值，web_agent.py:3710） | 随仓库文件 | 花费硬上限，必须底层校验；0 不能表示关闭 | P |
| autonomy | min_alarm_gap_minutes | veil_agent.py:1019 | toml 整数；当前 15（142） | 15 | 正整数分钟 | 仅改文件 | 只作用于 set_next_wake；太早的请求被顺延（autonomy.py:591-598） | 服务重启 | 随仓库文件 | 过小会让闹钟贴着当前时间 | A |
| autonomy | quiet_hours | veil_agent.py:1021（parse 734-751） | toml 字符串 HH:MM-HH:MM；当前 "01:00-06:00"（144） | ""（=关闭） | 00:00-23:59；首尾不能相同；允许跨夜 | 仅改文件（页面只显示字符串，不编辑） | 闹钟落窗口内直接拒绝（autonomy.py:601-603）；到期闹钟落入窗口标 missed | 服务重启 | 随仓库文件 | 改坏=服务起不来 | A |
| autonomy | quiet_hours_enabled | veil_agent.py:1022-1024 | toml bool；当前 true（145） | quiet_hours 是否配置（此处 true） | true/false | 活动页「开启/关闭夜间安静」→ /api/quiet-hours（chat.js:2065-2075）；写 data 覆盖本键 | data/<角色>/autonomy-quiet.json 存在时覆盖 toml（autonomy.py:840-847） | 立即（每次 due 读文件） | toml 随仓库；页面值存 data | 无 quiet_hours 时按钮禁用且本键无效（chat.js:1762） | U |
| autonomy | max_input_tokens | veil_agent.py:1028,1036-1037,4016-4024 | toml 整数；当前 100000（147） | 不设（=用 provider 上限） | 正整数 token | 仅改文件 | 与 autonomy 专用模型 provider 上限取小（4017-4019） | 服务重启 | 随仓库文件 | 设太大无效（provider 仍截断），太小会提前 TooLong | A |
| autonomy | run_budget_cny | veil_agent.py:1029,1039-1040；使用 5586,5635-5642 | toml 字符串；当前 "0.25"（149） | 不设（=无单次花费上限） | >0 人民币/次 | 仅改文件 | 与全局/分类日预算叠加；单次计数只算 autonomy 类别 | 服务重启 | 随仓库文件 | 缺省时单次只有 15 分钟与步数兜底 | A |
| autonomy | max_tool_steps | veil_agent.py:1030,1041；使用 5588,6213-6216 | toml 整数；当前 60（150） | 60（仅当 run_budget_cny 存在） | 正整数步 | 仅改文件 | 存在时压过 [tools].max_steps；没有 run_budget_cny 时整键被忽略 | 服务重启 | 随仓库文件 | 死键风险（见 §3.2） | A |
| autonomy | threads | veil_agent.py:1025-1027；使用 autonomy.py:1102-1128 | toml bool；当前 true（153） | True | true/false | 仅改文件 | 只控制唤醒提示的跨唤醒线索与已读未变文件两段；不影响权限 | 服务重启 | 随仓库文件 | 无 | A |
| autonomy | 自主活动总开关（ActivityState） | veil_agent.py:1177-1217；web_agent.py:3728 | data/<角色>/resident-state.json {"enabled":bool,"enabled_at":ISO|null} | 缺文件=false（暂停） | true/false | 活动页 #activity-toggle（chat.html:89；chat.js:2054-2064）→ /api/activity；CLI /pause /resume（veil_agent.py:6788-6795） | 最高优先级：关=自然唤醒与闹钟都不跑（autonomy.py:936-947） | 下一个工具步骤边界（should_yield 1276-1279）；按钮即时返回 | data 内，update 不动（.gitignore:10） | 关会中断正在跑的唤醒；写失败回滚并抛错（veil_agent.py:2713-2720） | U |
| autonomy | enabled_at（开关时间戳） | veil_agent.py:1212；autonomy.py:732-734 | 同上 json 字段 | null | ISO 时间 | 无直接入口（随开关写） | 自然唤醒计时基准 | 下次排期 | data | 手改会改变唤醒节奏 | R |
| autonomy | 今日已醒/下次唤醒/上次结果 | autonomy.py:971-993；web_agent.py:1904-1922 | 计算值（autonomy.jsonl 派生） | — | — | 活动页/页头/侧栏只读（chat.html:22-23,89；chat.js:1740-1758） | 只读 | 每次 /api/events | — | 无 | R |
| autonomy | 唤醒记录 autonomy.jsonl | autonomy.py:437,1422-1424 | data/<角色>/autonomy.jsonl 追加 jsonl | 无 | 结构校验失败=停止调度（463-487） | 活动页时间线只读 | 只读事实来源 | 每轮追写 | data | 损坏会导致 UserError「请保留 data 文件夹」 | R |
| autonomy | 运行中标记 autonomy-active.json | autonomy.py:438,1288-1299,783-834 | data/<角色>/autonomy-active.json | 无 | 崩溃后投影成失败记录 | 仅改文件（无入口） | 启动恢复 | 服务启动 | data | 中断记录不自动重试 | R |
| autonomy | MAX_RUNTIME_SECONDS | autonomy.py:17,1288,1338-1340 | 代码常量 900 秒 | 15 分钟 | 无配置 | 代码写死，无入口 | 与 run_budget/步数并列，先到先停 | 每轮 | 不适用 | 长工具调用不能立刻打断，只在边界检查 | P |
| autonomy | MAX_CONTEXT_BYTES | autonomy.py:18,1228-1247 | 代码常量 32768 字节 | 32KB | 无配置 | 代码写死，无入口 | 超限按固定顺序删块，最后保留规则文本 | 每轮拼装 | 不适用 | 删块顺序会悄悄丢上下文 | P |
| autonomy | MAX_CHAT_CONTEXT_BYTES | autonomy.py:19,131-137 | 代码常量 1500 字节 | 1500B | 无配置 | 代码写死，无入口 | 聊天交接卡压缩，最多丢工具动作 | 下一条聊天 | 不适用 | 交接可能只剩决定 | P |
| autonomy | WAKE_PREVIOUS_TOOL_LIMIT | autonomy.py:20,1157 | 常量 10 | 10 条 | 无配置 | 代码写死 | 上轮成功工具只带 10 条 | 每轮 | — | 无 | P |
| autonomy | MIN_NATURAL_GAP | autonomy.py:32,729 | 常量 20 分钟 | 20 | 无配置 | 代码写死 | 与 mean/3 取小，interval 很小时失效 | 每轮 | — | interval 可配到 1 分钟 | P |
| autonomy | QUIETER_FACTOR | autonomy.py:27,694 | 常量 2.5 | ×2.5 | 无配置 | 代码写死 | 节奏 quieter 时平均间隔放大 | 取节奏时 | — | 无 | P |
| autonomy | 自然节奏 autonomy-rhythm.json | autonomy.py:627-650 | data/<角色>/autonomy-rhythm.json {mode,until,set_at,reason,interval_minutes?} | 缺文件=normal | mode ∈ normal/quieter/silent/custom；custom 需 30-360 | 仅由 Peter 用 set_wake_rhythm 工具写（652-686）；无页面 | 覆盖自然间隔，但闹钟与 Lan 的日上限/安静时段仍优先（685-686） | 立即（每次读） | data | Peter 可自行降频；不能突破次数/预算 | A |
| autonomy | 节奏时长 minutes | autonomy.py:668-674 | 同上 until 字段 | — | 15–4320 分钟 | 同上工具参数 | 到期自动回 normal（647-648） | 到期 | data | 上限 72 小时 | A |
| autonomy | 节奏原因 reason | autonomy.py:671-674 | 同上 reason | — | 1–300 字 | 同上工具参数 | 会进唤醒提示 | 立即 | data | 无 | R |
| autonomy | 闹钟 autonomy-alarm.json | autonomy.py:493-625 | data/<角色>/autonomy-alarm.json {alarms:[...]} | 缺文件=无 | 结构校验失败=UserError | 仅由 set_next_wake 工具写；页面只显示 pending（autonomy.py:991-992） | 与自然唤醒取更早者（771-777）；安静时段的闹钟被跳过/标 missed | 立即 | data | Peter 可设 7 天内闹钟；Lan 的次数/安静时段压过它 | A |
| autonomy | MAX_ALARM_DAYS | autonomy.py:21,592,600 | 常量 7 天 | 7 | 无配置 | 代码写死 | 超过直接拒绝 | — | — | 无 | P |
| autonomy | MAX_ALARM_REASON_CHARS | autonomy.py:22,584-585,671 | 常量 300 字 | 300 | 无配置 | 代码写死 | 超长拒绝 | — | — | 无 | P |
| autonomy | MAX_PENDING_ALARMS | autonomy.py:25,606-608 | 常量 12 | 12 | 无配置 | 代码写死 | 满了拒绝新闹钟 | — | — | 防止无限排队 | P |
| autonomy | ALARM_HISTORY_LIMIT | autonomy.py:24,528 | 常量 10 | 10 | 无配置 | 代码写死 | 已完成闹钟只留 10 条 | — | — | 无 | P |
| autonomy | MIN/MAX_RHYTHM_MINUTES | autonomy.py:28-29,668 | 常量 15 / 4320 | 15–4320 | 无配置 | 代码写死 | 节奏时长边界 | — | — | 无 | P |
| autonomy | MIN/MAX_CUSTOM_INTERVAL | autonomy.py:30-31,643,677 | 常量 30 / 360 | 30–360 | 无配置 | 代码写死 | custom 节奏边界（可小于 interval 默认） | — | — | 自定义可把间隔压到 30 分钟 | P |
| autonomy | set_next_wake / set_wake_rhythm 工具 | veil_agent.py:2438,4819-4832 | 仅当 autonomy_controller 存在才提供（非 acceptance） | 提供 | — | 无页面开关 | acceptance 模式整组不提供（web_agent.py:3624-3625） | 每请求 | — | set_next_wake 受 activity 开关限制（579-580） | P |
| autonomy | autonomy 专用模型 | model_settings.py:21,480,560-562；veil_agent.py:3979-4013 | .env / models.json use.autonomy（role） | 跟随聊天（未写入 use 时） | 任意已配置模型或 follow chat | 设置页「自主活动」下拉 #use-autonomy（chat.html:191） | 与 [autonomy].max_input_tokens 取小；不改则用聊天模型 | 立即（下次请求切模型，veil_agent.py:3992-4013） | .env/models.json | 换贵模型更快撞日预算 | U |
| glance | enabled | veil_agent.py:1045；autonomy.py:1455-1456 | toml bool；当前 true（156） | False | true/false；还需 sys.platform==win32 | 活动页「开启/暂停瞄一眼」→ /api/glance（chat.js:2076-2084）；写 data 覆盖 | data/glance-settings.json 覆盖 toml（autonomy.py:1481-1492）；非 Windows 永远 false | 立即 | toml + data | 非 Windows 点按钮 409，页面提示「保存失败，检查连接」误导 | U |
| glance | min_interval_minutes | veil_agent.py:1046 | toml 整数；当前 15（157） | 15 | 正整数分钟 | 仅改文件 | 必须 ≤ max（1051） | 服务重启 | 仓库文件 | 页面不显示真实值（写死 15–40） | A |
| glance | max_interval_minutes | veil_agent.py:1047 | toml 整数；当前 40（158） | 40 | 正整数分钟，≥min | 仅改文件 | 同上 | 服务重启 | 仓库文件 | 同上 | A |
| glance | idle_seconds | veil_agent.py:1048；autonomy.py:1459,1519 | toml 整数；当前 300（159） | 300 | 正整数秒 | 仅改文件 | 桌面空闲小于该值才算在用电脑 | 服务重启 | 仓库文件 | 过小几乎不触发，过大在 Lan 忙时打扰 | A |
| glance | max_steps | veil_agent.py:1049-1052；autonomy.py:1460 | toml 整数；当前 4（160） | 4 | 1–4（>4 报 invalid glance configuration） | 仅改文件 | 与 glance_tool_limit 取小（veil_agent.py:5589） | 服务重启 | 仓库文件 | 只能调小，调大无效 | A |
| glance | daily_budget_cny | veil_agent.py:1050；web_agent.py:3711-3712 | toml 字符串；当前 "1"（161） | "1" | >0 人民币/日 | 仅改文件 | 独立分类预算；同时计入全局日/月 | 服务重启 | 仓库文件 | Codex 提醒也吃这个池（codex_watch.py:417-419） | P |
| glance | glance-settings.json | autonomy.py:1463,1497-1504 | data/<角色>/glance-settings.json {"enabled":bool} | 缺文件=true（1481-1492） | true/false | 活动页 toggle | 覆盖 toml enabled | 立即 | data | 手改可绕过 toml | U |
| glance | glance-last.json / glance-delivered.json | autonomy.py:1462,1684-1688,407-429 | data/<角色>/ 两个 json | 无 | — | 只读进聊天上下文 | 只交付一次 | 下次聊天 | data | 含前台窗口标题/应用名（隐私） | R |
| glance | recent-experiences.json | recent_experiences.py:8-9,20-46 | data/<角色>/recent-experiences.json | 最多 5 条 / 18000B | 无配置 | 代码写死 | 只保留最近一次瞄一眼 | 每次瞄一眼替换 | data | 含屏幕摘要 | R |
| glance | Windows 平台限制 | autonomy.py:1455-1456 | 代码常量 | win32 | 无配置 | 代码写死，无入口 | 非 Windows 一律关闭 | — | — | 防止在别的平台调 Windows API | P |
| codex | Codex 监视开关 | codex_watch.py:111-119,135-154；web_agent.py:3216-3227 | data/<角色>/codex-watch-settings.json {"enabled":bool} | 缺文件=true（开） | true/false | 活动页「开启/暂停监视」→ /api/codex-watch（chat.js:2085-2096） | 独立于 activity 开关（356-359） | 立即（重置扫描状态） | data | 默认开启容易忽略；关掉会丢 pending | U |
| codex | POLL_SECONDS | codex_watch.py:16,356-362 | 常量 10 秒 | 10 | 无配置 | 代码写死（页面写死 检查间隔 10 秒，chat.html:92） | — | 每 10 秒 | — | 无 | P |
| codex | STALE_SECONDS | codex_watch.py:17,333,344 | 常量 600 秒 | 10 分钟 | 无配置 | 代码写死 | 超时发「状态待确认」提醒 | — | — | 可能误报 | P |
| codex | MAX_SCAN_BYTES / MAX_LINE_BYTES | codex_watch.py:18-19,285-299 | 常量 8MB / 8MB | 8MB | 无配置 | 代码写死 | 单次扫描上限，超出留到下轮 | — | — | 无 | P |
| codex | MAX_REMEMBERED_EVENTS | codex_watch.py:20,199,264 | 常量 500 | 500 | 无配置 | 代码写死 | 去重窗口 | — | data（state 文件） | 清空 state 会重复提醒 | P |
| codex | 会话目录 sessions_root | codex_watch.py:72-73 | %USERPROFILE%/.codex/sessions | 环境变量/家目录 | 无配置 | 代码写死，无入口 | — | 每轮扫描 | — | 只读打开，拒绝符号链接（176-180） | P |
| background | LIMITS（段落/字数/块/关键词） | background.py:16-17,128,155-171,225,229-237 | 代码常量 | items=3, characters=1500, chunk=700, chunks_per_paragraph=2, keywords=30, keyword_characters=40 | 无配置 | 代码写死，无入口 | 每次请求按当前输入选段 | 每请求 | — | 只影响提示词长度 | P |
| background | CONTEXT_LOG_KEEP | background.py:18,304-318 | 常量 500 行 | 500 | 无配置 | 代码写死 | context-log.jsonl 超 500+100 才裁剪 | 每请求后 | data | 无 | P |
| short_term_notes | enabled | veil_agent.py:1000-1007；2370-2375 | toml bool；当前 false（237） | False | true/false | 仅改文件 | 关=不构造不调度，聊天也不用便签上下文 | 服务重启 | 仓库文件 | 开启会计费（独立 category） | A |
| short_term_notes | INTERVAL / INACTIVE_FOR | short_term_notes.py:13-14,135,150,205-227 | 常量 30 分钟 / 60 分钟 | 30 / 60 | 无配置 | 代码写死 | 便签 60 分钟后过期 | 每 30 分钟 | — | 无 | P |
| short_term_notes | MAX_CONTEXT_NOTES / CHARACTERS、MAX_NOTE_CHARACTERS、MAX_OPERATIONS、MAX_OUTPUT_TOKENS、MAX_REQUEST_BYTES | short_term_notes.py:15-20,96,247,397,486,540 | 常量 10 / 800 / 240 / 50 / 512 / 20000 | 同左 | 无配置 | 代码写死 | 请求预算另与 provider 一半取小 | 每次提取 | — | 超限截断/拒绝 | P |
| experience | enabled | experience_memory.py:63,84-104,133-145；veil_agent.py:2378-2383 | toml bool + data/<角色>/experience-memory.json 覆盖 | False | true/false | 记忆页「开启/关闭自动整理」→ /api/experience-memory（chat.js:942-948） | data 开关覆盖 toml | 立即 | toml + data | 页面开关优先 | U |
| experience | min_chars | experience_memory.py:63,295 | toml int；当前 3000（91） | 3000 | 非负整数 | 仅改文件 | 与 idle_minutes 同时满足才触发 volume | 服务重启 | 仓库文件 | 改小=更频繁花钱 | A |
| experience | idle_minutes | 同上 63,295 | toml int；当前 30（92） | 30 | 非负整数 | 仅改文件 | 同上 | 服务重启 | 仓库文件 | 0 会在说话中途整理 | A |
| experience | floor_chars | 同上 63,297 | toml int；当前 200（93） | 200 | 非负整数 | 仅改文件 | 小于此值永不整理 | 服务重启 | 仓库文件 | 死区最小量 | A |
| experience | day_end | 同上 63,95-96,299-304 | toml HH:MM；当前 "23:30"（94） | "23:30" | 合法 HH:MM | 仅改文件 | 过点后放宽到 floor_chars | 服务重启 | 仓库文件 | 写错=启动失败 | A |
| experience | day_end_idle_minutes | 同上 63,302 | toml int；当前 10（95） | 10 | 非负整数 | 仅改文件 | 日终分支的空闲门槛 | 服务重启 | 仓库文件 | 无 | A |
| experience | daily_budget_cny | 同上 63,97-100,549,610 | toml 字符串；当前 "1"（96） | "1" | ≥0（允许 0 关闭花费） | 仅改文件 | 独立分类日预算，也计入全局 | 服务重启 | 仓库文件 | 与 autonomy 的 >0 规则不同 | P |
| experience | recent_days / recent_chars | 同上 63-65（DEFAULTS） | toml int；当前 3 / 1500（97-98） | 3 / 1500 | 非负整数 | 仅改文件 | 放在聊天历史前的近期条目 | 服务重启 | 仓库文件 | 无 | A |
| experience | TICK_SECONDS | experience_memory.py:36,160-166 | 常量 300 秒 | 5 分钟 | 无配置 | 代码写死 | 每次 tick 只判断一次 | 每 5 分钟 | — | 无 | P |
| experience | BACKOFF（失败退避） | experience_memory.py:49-50,306-320 | 常量 30 分钟、2 小时、次日 | 同左 | 无配置 | 代码写死 | 连续失败降频，不跳过聊天 | 失败后 | data 事件 | 防止反复烧钱 | P |
| experience | REQUEST_CHARS / MAX_OUTPUT_TOKENS / CANDIDATE_CHARS | experience_memory.py:43-44,58,374,545 | 常量 12000 / 8000 / 6000 | 同左 | 无配置 | 代码写死 | 单次请求预算 | 每次整理 | — | 无 | P |
| experience | experience-memory-failures.jsonl | experience_memory.py:46-48,322-336 | data/<角色>/ 保留最近 20 条失败原文 | 20 | — | 只读排查 | 失败诊断 | 失败时 | data | 含模型原始输出（可能含隐私） | R |
| checkpoint | [context].work_checkpoint | veil_agent.py:1152-1155；使用 3411-3420,4241-4244 | toml bool；当前 true（135） | True | true/false；非 bool 报错 | 仅改文件 | false 停止写入/注入；页面仍可结束已有条目（web_agent.py:3179-3193） | 服务重启 | 仓库文件 | 关掉不影响自主活动（自主有自己的记录） | A |
| checkpoint | MAX_CONFIRMED/UNCONFIRMED/FAILED、MAX_GOAL_CHARS、KEEP_FOR | work_checkpoint.py:15-19,131,159-167,171-179 | 常量 6/4/3、300、48 小时 | 同左 | 无配置 | 代码写死 | 只投影真改过东西的回合 | 每轮/每次读取 | data/work-checkpoint.json | 48h 后不再注入 | P |
| checkpoint | MAX_EVIDENCE、FRESH_FOR、INTERRUPTED | investigation_checkpoint.py:14-16,103-121,156 | 常量 16、15 分钟、5 种中断原因 | 同左 | 无配置 | 代码写死 | 阻止重放已完成副作用 | 每次工具调用 | data/investigation-checkpoint.json | 安全约束 | P |
| tasks | MAX_PROGRESS_ITEMS / MAX_TOOL_EVENTS | long_tasks.py:18-19,339-367 | 常量 40 / 12 | 同左 | 无配置 | 代码写死 | 超出滚动丢弃 | 每次更新 | data/long-task.json | 历史被截断 | P |
| tasks | STALE_TASK_DAYS | autonomy.py:148-150,1039-1046,1176-1180 | 常量 3 天 | 3 | 无配置 | 代码写死 | 超期任务不进唤醒上下文 | 每轮 | data（不删） | 会看起来没任务 | P |
| reply_later | reply_later_max_hours | veil_agent.py:922,3191-3193 | toml 整数（[tools] 下，当前未写） | 24 | 正整数小时 | 仅改文件 | 超时拒绝 | 服务重启 | 仓库文件 | 过大=无限期挂起 | A |
| reply_later | reply_later_max_deferrals | veil_agent.py:923,3205-3206 | 同上 | 2 | 正整数次 | 仅改文件 | 同一批消息最多推迟次数 | 服务重启 | 仓库文件 | 无 | A |
| reply_later | reply_later_max_pending | veil_agent.py:924,3201-3202 | 同上 | 3 | 正整数条 | 仅改文件 | 同时挂起的提醒数 | 服务重启 | 仓库文件 | 无 | A |
| reply_later | reply_later_daily_wakes | veil_agent.py:925,3280-3282 | 同上 | 20 | 正整数次/日 | 仅改文件 | 每日提醒唤醒上限 | 服务重启 | 仓库文件（wake_log 在 reply-later.json） | 会额外唤醒，受安静时段顺延（3290-3294） | A |
| calendar | 轮询间隔 | web_agent.py:1378-1379 | 代码常量 5 秒 | 5 | 无配置 | 代码写死 | 日历/提醒/reply_later 共用 | 每 5 秒 | — | 无 | P |
| calendar | 每日待办提醒窗口 | life_records.py:296-319；web_agent.py:1381-1382 | 代码常量 23:30 起 6.5 小时 + Lan 空闲 ≥1200 秒 | 23:30–06:00 | 无配置 | 代码写死 | 每本地日只领一次 | 当日 | data/calendar-daily-reminders.jsonl | 空闲判断用 last_interaction（启动即视为已空闲，web_agent.py:221） | P |
| calendar | 提醒迟到判定 | life_records.py:232-264 | 代码常量 late>3600 秒 = missed | 1 小时 | 无配置 | 代码写死 | 关服务期间的提醒补一次并标 missed | 启动后首次轮询 | data/calendar-reminders.jsonl | 无 | P |
| calendar | 每日顺口提一句 | life_records.py:321-341 | 代码常量 23:50–06:00 | 同左 | 无配置 | 代码写死 | 只提示一句，不单独发消息 | 当日 | data/calendar-daily-hints.jsonl | 无 | P |
| calendar | 聊天约定自动记日历 | life_records.py:106-119；calendar_agreements.py:29-33,85 | data/<角色>/life-settings.json（auto_record） | 开（缺文件时） | true/false | 日历页按钮「聊天约定自动记日历：开/关」→ /api/life（chat.js:1272；web_agent.py:3254-3258） | 关=不发生成调用 | 立即 | data | 每次命中跑一次小模型（512 tokens） | U |
| calendar | 自动记录窗口 | calendar_agreements.py:60-62,91-94 | 代码常量 今天~+60 天 | 60 天 | 无配置 | 代码写死 | 超出不记录 | 每次判断 | — | 无 | P |
| channel | email poll_seconds / daily_max_sent | veil_agent.py:1094-1102；email_channel.py:91-92,130-132,185-186 | toml [email]；当前 60 / 20（192-193） | 60 / 20 | 正整数秒 / 正整数封 | 仅改文件 | 每日计数按本地日 | 服务重启（通道启动时构造，web_agent.py:3725-3727） | 仓库文件 | 与 .env 三件套联动；无 .env=通道不启动 | A |
| channel | email smtp/imap host/port | veil_agent.py:1099-1102 | toml；默认 smtp.qq.com:465 / imap.qq.com:993 | 同左 | 正整数端口 | 仅改文件 | — | 服务重启 | 仓库文件 | 改错=收不到信 | A |
| channel | qq poll_seconds / idle_seconds / daily_max_sent | veil_agent.py:1103-1108；qq_channel.py:206-208,236-253,292-293 | toml [qq]；当前 20 / 120 / 100（199-202） | 20 / 120 / 100 | 正整数 | 仅改文件 | idle 未到不读不写；全屏不弹 | 服务重启 | 仓库文件 | 需要 QQ 登录窗口；依赖 .env QQ_LAN_CONTACT | A |
| channel | QQ 全屏保护 | qq_channel.py:251-253 | 代码常量 | 开 | 无配置 | 代码写死 | 全屏视频时不抢焦点 | 每次 | — | 安全/礼貌约束 | P |
| channel | AwayBridge 队列节奏 | away_bridge.py:152-155,194-201 | 代码常量 2 秒 / channel.poll_seconds | 2 秒 | 无配置 | 代码写死 | 发送线程等待 | 持续 | — | 无 | P |
| screen_pop | 文本/时长/数量上限 | screen_pop.py:11-13,53-77 | 代码常量 500 字、1–20 秒（默认 12）、popup 1–10 层 | 同左 | 无配置 | 代码写死，无入口 | Codex 提醒只用 popup（codex_watch.py:461） | 每次调用 | — | 防止刷屏 | P |
| screen_pop | Codex 投递的桌面空闲阈值 | codex_watch.py:455 | 代码常量 300 秒（写死，不读 [glance].idle_seconds） | 300 | 无配置 | 代码写死 | 与 glance 的 idle 配置脱钩 | 每次投递 | — | 配置改了这里不跟着变 | P |

---

## 3. 重复 / 冲突 / 隐性覆盖

### 3.1 同一参数多处来源
1. 每日自主预算：Budget.__init__(autonomy_daily=Decimal('2'))（veil_agent.py:1329）+ [autonomy].daily_budget_cny 默认 "2"（1020）+ 启动赋值（web_agent.py:3710）。实际生效=character.toml 值（Peter 为 3）。三处一致是巧合而非同源，改一处不会自动同步。
2. 瞄一眼预算：Budget 默认 1（1329）+ [glance].daily_budget_cny 默认 "1"（1050）+ 启动赋值（3711-3712）。
3. 自主步数：[tools].max_steps（默认 6，veil_agent.py:916）与 [autonomy].max_tool_steps（默认 60，1041）并存；只有 run_budget_cny 存在时后者才生效，否则静默退回前者（5587-5588）。最容易改了没反应。
4. 自主/瞄一眼模型：models.json role（use.autonomy/use.glance，未写=跟随聊天；model_settings.py:560-562）与 character.toml provider 叠加；活动跑时临时替换 cfg.provider 再还原（veil_agent.py:4002-4013）。
5. 活动开关：resident-state.json（页面）与 [autonomy] 无字段冲突，但开关与 [autonomy].quiet_hours_enabled 的持久化位置完全不同（data vs 仓库文件），备份策略不同。
6. Glance 开关：[glance].enabled（仓库）与 glance-settings.json（data）双源，后者覆盖；删 data 文件会回到 toml 值（1481-1492）。
7. Experience 开关：[memory.experience].enabled（仓库）与 experience-memory.json（data）双源，后者覆盖（133-145）。
8. 安静时段开关：[autonomy].quiet_hours_enabled 与 autonomy-quiet.json 双源，后者覆盖；quiet_hours 未配置时两者都无效。
9. Codex 开关：只有 data 一个来源，但缺文件默认开（codex_watch.py:111-119），与 Glance/Activity 的缺文件默认关相反，容易被误判。
10. 日预算的全局与分类：同一笔自主花费同时进 category=autonomy 与全局 day/month（veil_agent.py:1544-1553）；聊天只受全局限制，所以 autonomy 撞上限时聊天照常。

### 3.2 死键 / 只解析不生效
- max_tool_steps 单独设置无效：1039-1041 只在 run_budget_cny 非 None 时读取。
- glance.max_steps > 4 无法生效：load_character 直接报 invalid glance configuration（1051-1052），运行期又 min(…,4)（autonomy.py:1460）；该键实际只有 1–4 的意义。
- quiet_hours_enabled 在 quiet_hours 为空时无效：quiet_enabled() 第一行 if not self.quiet_hours: return False（836-838），set_quiet_enabled 返回 False（850-851），页面按钮也禁用（chat.js:1762）。
- threads=false 只是不拼两段提示（1102-1128），不影响执行。
- [autonomy].max_input_tokens 若大于 provider 上限则无效果（4017-4019 取 min）。
- [glance].enabled=false 与非 Windows 双保险：非 Windows 下即使 toml true 也永远 false（1455-1456）。

### 3.3 页面值与实际值可能不一致
- chat.js 把瞄一眼间隔写死为「已开启（15–40 分钟）」（chat.js:1764）；改 min/max_interval_minutes 后页面不更新。
- chat.html 把 Codex 检查间隔写死为「10 秒」（chat.html:92），与 POLL_SECONDS 同值但非配置。
- 页头/侧栏「下次唤醒」有多个渲染点（chat.html:23,40,308；chat.js:1747-1749），都以 s.next_wake_at 为准，属正常重复显示。
- 活动页「今日已醒 x / y」的 y 来自 daily_max_runs（autonomy.py:987），x 来自 _eligible_count（907-909）；该计数把闹钟唤醒和自然唤醒一起算，与「自然间隔」直觉不同。
- 非 Windows 点「开启瞄一眼」会失败，但页面只提示「保存失败，检查 Agent 连接」（chat.js:2082），误导为网络问题。

### 3.4 隐性覆盖
- 安静时段窗口判断用当天/前一天双窗口（autonomy.py:872-883），跨夜配置正确；但 _next_wake_details 只把自然唤醒推到窗口结束（767-770），不会把闹钟推后——闹钟落窗口内直接作废（773-774, 924-933）。
- activity 关闭时，正在跑的唤醒在下一个工具步骤让位（1276-1279, 5644-5652），已发出的消息不回滚。
- Codex 提醒不受 activity 开关约束，却消耗 glance 日预算（codex_watch.py:356-359, 417-419）；Glance 关掉后 Codex 仍可提醒。
- reply_later 到期唤醒不查 activity：claim_due_reply_later 只查每日次数与安静时段（3280-3294），因此自主活动暂停时 reply_later 仍会唤醒。
- 每日待办提醒由 hub 轮询触发（web_agent.py:1381-1393），同样不受 activity 开关影响。
- [memory.experience] 未知键直接报错（experience_memory.py:89-90），与 [autonomy] 忽略未知键不同；拼错 experience 键会导致服务起不来，拼错 autonomy 键会静默忽略。

---

## 4. 页面与入口现状

| 参数 | 现状入口 | 判定 |
|---|---|---|
| 自主活动开关 | 活动页 #activity-toggle（chat.html:89）→ /api/activity（web_agent.py:3421） | 有页面，可改 |
| 夜间安静开关 | 活动页 #quiet-toggle（chat.html:90）→ /api/quiet-hours（3429） | 有页面，可改（无 quiet_hours 时禁用） |
| 瞄一眼开关 | 活动页 #glance-toggle（chat.html:91）→ /api/glance（3423） | 有页面，可改 |
| Codex 监视开关 | 活动页 #codex-watch-toggle（chat.html:92）→ /api/codex-watch（3452） | 有页面，可改 |
| 经历自动整理开关 | 记忆页 #experience-toggle（chat.html:131；chat.js:942-948）→ /api/experience-memory（3431） | 有页面，可改 |
| 聊天约定自动记日历 | 日历页按钮（chat.js:1272）→ /api/life（3254-3258） | 有页面，可改 |
| 自主活动用的模型 | 设置页 #use-autonomy（chat.html:191）→ /api/models（2825-2828） | 有页面，可改 |
| 瞄一眼用的模型 | 设置页 #use-glance（chat.html:195） | 有页面，可改 |
| reply_later 上限 | 无 | 仅改文件（[tools]） |
| interval_minutes / daily_max_runs / 日预算 / 单次预算 / 步数 / 输入窗口 / threads | 无 | 仅改文件（character.toml） |
| 安静时段时间范围 | 页面只显示字符串（chat.js:1760） | 仅改文件 |
| 瞄一眼间隔/空闲/步数/预算 | 页面只显示写死的 15–40 分钟 | 仅改文件 |
| 自然节奏（mode/minutes/reason） | 只有 Peter 的 set_wake_rhythm 工具 | 无页面；Lan 不能直接改 |
| 闹钟列表 | 页面只读显示（autonomy.py:991-992） | 不可编辑 |
| 长期任务 | 日历页以「计划」只读展示（web_agent.py:2294-2313）；工具 long_task_update 写 | 只读页面 |
| work checkpoint | 上下文页可「结束」（web_agent.py:3179-3193） | 有限入口，不能改参数 |
| 代码写死、无入口 | MAX_RUNTIME_SECONDS、MAX_CONTEXT_BYTES、MAX_CHAT_CONTEXT_BYTES、MIN_NATURAL_GAP、QUIETER_FACTOR、THREAD/READ limit、MAX_ALARM_*、MIN/MAX_RHYTHM/CUSTOM、glance 平台限制、Codex POLL/STALE/SCAN/REMEMBERED、后台 LIMITS/CONTEXT_LOG_KEEP、便签 INTERVAL/MAX_*、经历 TICK/BACKOFF/REQUEST_CHARS、checkpoint 各上限、任务 40/12、日历 5 秒与 23:30/23:50、screen_pop 上限、AwayBridge 2 秒 | 代码写死，无入口 |

补充：诊断页只在一轮请求的链路里显示自主活动结果（diagnostics-page.js:293-296），只读排查；peter-page.js 与本域无任何键（全文 0 匹配）。

---

## 5. 生效时机与持久化

| 层 | 保存位置 | 何时生效 | 重启后 | 更新/备份后 |
|---|---|---|---|---|
| 角色配置 [autonomy]/[glance]/[tools]/[memory.experience]/[short_term_notes]/[email]/[qq] | characters/<角色>/character.toml（仓库跟踪） | 服务重启（load_character 只在 web_agent.py:3703 / CLI 6890 调用；无热加载） | 保留 | 取决于 update-main 冲突处理；本次未审计（见 §7） |
| 页面开关：活动/安静/瞄一眼/Codex/经历/日历自动记录 | data/<角色>/resident-state.json、autonomy-quiet.json、glance-settings.json、codex-watch-settings.json、experience-memory.json、life-settings.json | 立即（多数每次读文件；activity 在下一个工具步骤） | 保留（data/ 被 .gitignore:10 排除，更新不碰） | 保留；恢复备份即恢复开关 |
| 节奏/闹钟 | autonomy-rhythm.json / autonomy-alarm.json | 立即（每次读） | 保留（节奏有 until，过期自动回 normal） | 保留 |
| 自主记录/工具记录/费用 | autonomy.jsonl、tools.jsonl、costs.jsonl | 追加即生效 | 保留 | 保留；costs.jsonl 参与日/月预算 |
| 经历整理开关与事件 | experience-memory.json / experience-memory.jsonl | 开关立即；整理线程每 5 分钟 | 保留 | 保留 |
| 模型选择 | .env + models.json（model_settings） | 立即（switch_model，web_agent.py:1497-1499） | 保留 | 保留（.env 被 .gitignore:2） |

关键点：
- 没有「保存并生效」按钮：所有 character.toml 改动必须重启服务（活动页没有编辑入口）。这与「全局管理中心」即改即用的预期冲突，施工前要先定语义。
- 页面开关失败会回滚：activity 先写文件再写能力记录，记录失败回滚（veil_agent.py:2713-2720）；quiet 同理（autonomy.py:853-863）。
- 备份恢复：data/ 内开关、闹钟、节奏、记录都在备份范围；仓库内 character.toml 若在恢复时被覆盖，max_tool_steps/run_budget_cny 等会回到备份时点。

---

## 6. 风险与建议分类

1. U（可直接给用户）：activity 开关、夜间安静开关、瞄一眼开关、Codex 开关、经历自动整理开关、日历自动记录开关、自主/瞄一眼模型选择。
2. A（高级，需说明影响）：interval_minutes、daily_max_runs、min_alarm_gap_minutes、max_input_tokens、run_budget_cny、max_tool_steps、threads、[glance] 四个间隔/步数、reply_later 四项、[memory.experience] 六个阈值、email/qq 轮询与日上限。
3. P（程序安全约束，不给用户改）：所有日/月预算硬上限（含 autonomy/glance/experience 分类与全局）、MAX_RUNTIME_SECONDS、MAX_CONTEXT_BYTES、MAX_PENDING_ALARMS、MAX_ALARM_DAYS、MIN/MAX_RHYTHM、MIN/MAX_CUSTOM_INTERVAL、Codex POLL/STALE/SCAN、后台 LIMITS、便签/经历/checkpoint/任务各上限、screen_pop 上限、日历 1 小时 missed 判定、Windows 平台限制。预算类虽在 toml 里，但代码是硬拒绝（BudgetExceeded），管理中心只能查看 + 走审批改文件，不能在页面随手放宽。
4. R（只读运行信息）：下次唤醒/原因、今日已醒/上限、上次结果与记录、闹钟列表、节奏文案、瞄一眼上次结果、Codex 状态、经历上次结果/花费、长期任务、work checkpoint。
5. S（敏感）：glance-last.json / recent-experiences.json / 经历失败原文含屏幕内容或模型原文，属隐私数据；页面只应显示摘要，导出要脱敏。变更 activity/quiet 会写能力记录（可审计，veil_agent.py:2715-2717；autonomy.py:858-860）。
6. X（冗余/废弃/待清理）：
   - activity.json 不存在：生产代码只用 resident-state.json（web_agent.py:3728；veil_agent.py:6632）；tests/test_capability_transactions.py:131 用 activity.json 只是测试自造名。管理中心不要把文件名建成 activity.json。
   - max_tool_steps 独立设置无效（依赖 run_budget_cny）→ 合并成一个「单次上限」对象，或在页面成对校验。
   - glance.max_steps > 4 永远不生效 → 限 1–4 并标注。
   - threads 实际只是提示词开关 → 改名或标「提示词」。
   - 页面写死的 15–40 分钟、10 秒 → 改读配置/常量。
   - [autonomy].quiet_hours_enabled 与 data 覆盖并存 → 管理中心只暴露一个值，并显示「由页面值覆盖」。

---

## 7. 无法确认项（明确原因）

1. 当前实际运行值（活动/quiet/glance/Codex/经历开关、闹钟与节奏内容、今日已醒次数、今日已花）：快照不含 data/（.gitignore:10），按只读边界也不能读正式运行目录 正式运行目录。只给代码默认值与仓库 toml。
2. 正式运行用的 character.toml 是否与仓库一致：快照里的是仓库版本；正式目录可能被本机改过，未读，无法对比。
3. update-main 更新后 character.toml 是否保留：update-main.cmd / update.bat 的冲突处理未审计；data/ 因 gitignore 一定保留，仓库跟踪的 toml 是否被覆盖无法确认。
4. 备份恢复（backup_restore.py）对本域 data 文件的具体处理：未逐行审计；只能确认这些文件都在 data/<角色>/ 下。
5. quiet_hours 在正式实例是否配置：只能确认仓库 toml 为 "01:00-06:00"、enabled=true；若正式 toml 不同，页面按钮可用性会不同。
6. 瞄一眼在正式实例是否真会触发：还取决于 Windows 解锁/空闲检测（windows_observer/desktop_access），本次未验证运行时权限与多显示器行为。
7. Codex 会话目录是否存在：取决于本机是否装 Codex；%USERPROFILE%/.codex/sessions 未在快照内。
8. hub 的 last_interaction 初值对首次每日提醒的影响：web_agent.py:221 初始化为 20 分钟前，因此服务刚启动就可能满足提醒空闲条件；实际是否立刻提醒还取决于当日是否已领过（life_records.py:302-307），未在真实数据上验证。
9. 经历自动整理在缺少 memory 时的行为：Chat.experience 仅当 self.memory is not None 才创建（veil_agent.py:2378-2383），无 memory 时页面 available 为 false（experience_memory.py:1116）；正式实例是否启用记忆未确认。
10. max_steps 与工具组预算（tool_groups.py BUDGET）的交互未纳入本域取证，可能与 [autonomy].max_tool_steps 的体感上限叠加，未验证。
