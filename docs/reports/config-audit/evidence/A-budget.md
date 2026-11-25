# A 域取证：预算与费用（VEIL main = e7e7992 快照）

> 只读审计。源码根：main 快照。本文件是唯一写入。
> 未读取正式运行目录 veil-agent、任何 data/、.env，因此「当前实际生效值」一律写「无法读取（禁止读运行数据）」，不做猜测。
> 行号为该快照中的实际行号；引用的界面文案、报错文案一律照抄源码。

---

## 1. 执行链

### 1.1 全局日/月上限：models.json → 运行期 Budget
1. 读取：ModelSettings.__init__ 读 data/<角色>/models.json，_clean() 调 _limits()（_limits 定义在 model_settings.py:443-449）。
2. 校验（model_settings.py:443-449）：
   - daily_cny 缺省 DEFAULT_DAILY=10（model_settings.py:29），上限 Decimal(1000)；
   - monthly_cny 缺省 DEFAULT_MONTHLY=300（model_settings.py:30），上限 Decimal(30000)；
   - monthly < daily 时抛 SettingsError「每月上限不能比每天上限小。」（model_settings.py:446-447）；
   - 单值由 _decimal() 校验：非数字「{label}要填数字。」；<=0「{label}要大于 0。」；超上限「{label}不能超过 {maximum}。」（model_settings.py:36-52）。
3. 暴露：ModelSettings.limits() 在 limits 为空时返回 (10, 300)，否则返回 Decimal(limits.daily_cny / monthly_cny)（model_settings.py:568-573）；public() 把 limits 发给页面（model_settings.py:601-613）。
4. 装载：命令行 main() 先 Budget(store)，再 budget.daily, budget.monthly = cfg.models.limits()（veil_agent.py:6889-6894）；网页入口同样（web_agent.py:3702, 3713）；--acceptance 再夹 min(配置, 2)/min(配置, 20)（web_agent.py:3715-3719）。
5. 真正使用点：每次模型请求在 Chat.send 里 self.budget.reserve(tool_cfg)（veil_agent.py:4493）；Budget.reserve 依次走 _require_verified（1366-1370）、totals（1493）、_enforce_category_budget（1448-1458），再做全局判定 if day + pending + amount > self.daily_limit() or month + pending + amount > self.monthly_limit()（1552-1553）。
6. 耗尽行为：抛 BudgetExceeded，文案「本次费用上限会超过 ¥{daily_limit}/天或 ¥{monthly_limit}/月，未发送。输入 /cost 查看。」（veil_agent.py:1553）；网页/命令行聊天循环捕获后继续，不退出进程（veil_agent.py:6852-6859）。
7. 非法值：费用上限无效会让整份模型配置加载失败，报「模型配置 models.json 的费用上限无效：{exc}；原文件已保留。」（model_settings.py:377-381），不静默回落。

### 1.2 分类预算：autonomy / glance
1. 配置：autonomy.daily_budget_cny（character.toml:141；缺省 '2' 见 veil_agent.py:1020）、glance.daily_budget_cny（character.toml:161；缺省 '1' 见 veil_agent.py:1050），都由 price() 校验「有限且 >0」（veil_agent.py:754-758）。
2. 装载：Budget.__init__(daily=10, monthly=300, autonomy_daily=2, glance_daily=1)（veil_agent.py:1327-1339），启动后立即被角色配置覆盖（veil_agent.py:6891-6893 / web_agent.py:3710-3712）。
3. 使用：Budget.autonomous() / glancing() 把 category+character 放进线程本地（veil_agent.py:1372-1394）；自主活动 with self.chat.budget.autonomous(...)（autonomy.py:1313），瞄一眼 with self.chat.budget.glancing(...)（autonomy.py:1661）。
4. 判定：_enforce_category_budget（veil_agent.py:1448-1458）用 category_totals（已花+待核对）比对 cap；超限文案「{label}已达到 ¥{cap}/日上限，聊天不受影响。」（1457），label 为「瞄一眼」或「自主活动」。
5. 瞄一眼额外：category pending>0 且触顶时先 settle_stale_glance()；STALE_GLANCE_SECONDS=600（veil_agent.py:1460），超过 600 秒的挂起预留按「已发出=全额、未发出=0」结算（1461-1490；1450-1451 触发）。
6. 耗尽行为：自主活动记 result='搁置'，result_reason「后台预算池或程序总预算已到上限；正常聊天仍可继续。」（autonomy.py:1344-1346）；瞄一眼只把错误写进 result_reason（autonomy.py:1669-1670），不重试。
7. 单次上限：自主活动命中 run_budget_cny 时 autonomy_cost_limited=True（veil_agent.py:5630-5642），文案「达到每次 ¥{cap} 的花费上限。」（autonomy.py:1347-1350）；步数文案「达到每次 {limit} 个工具步骤的硬上限。」（autonomy.py:1351-1356）；硬时限 MAX_RUNTIME_SECONDS = 15 * 60（autonomy.py:17），文案「达到每次 15 分钟的硬时限。」（autonomy.py:1339-1341）。

### 1.3 单次任务（工作台）与临时提额
1. 配置：executor.run_budget_cny 默认 '0.5'（veil_agent.py:1142；同值 DEFAULT_LIMITS，executor_opencode.py:40）；wake_budget_cny 默认 '0.3'（veil_agent.py:1143）；max_seconds 默认 600（veil_agent.py:1140 / executor_opencode.py:40）；repeat_limit 默认 3（veil_agent.py:1141）。
2. 组装：limits = {k: settings[k] for k in ('max_seconds','repeat_limit','run_budget_cny')}；自主活动下强制 run_budget_cny = min(run_budget, wake_budget)（veil_agent.py:5069-5071）。
3. 申请：仅当 budget_cny is not None and not autonomy 才走 _approve_run_budget（veil_agent.py:5072-5073）——自主活动不能申请提额。
4. 审批校验：非法金额「budget_cny must be a number of yuan.」；<=0 或 >MAX_RUN_BUDGET_REQUEST（=50，veil_agent.py:1324）时「budget_cny must be more than 0 and at most 50 yuan.」（veil_agent.py:4998-5003）；wanted <= current 直接放行（5005-5007）。
5. 差额算法：day_room, month_room = self.budget.headroom()（1360-1363：日上限-已花-pending、月上限-已花-pending）；shortfall = max(wanted-day_room, wanted-month_room, 0)（5008-5009）；卡上原文含「并把今天的总上限（¥X/天、¥Y/月）临时提高 ¥Z，仅限今天」（5010-5012）。
6. 批准：self.approval(...)，文案开头「Peter 想把这次工作台任务的费用上限从 ¥{current} 提到 ¥{wanted}（只管这一次任务，用多少花多少）」；只认 y（5013-5018）；批准后 raise_today(shortfall)，并把本次 limits['run_budget_cny'] 改成 wanted（5019-5021）。
7. 提额语义：raise_today 只存内存 _raise=(day, previous+amount)（1343-1349）；daily_limit() 与 monthly_limit() 都加这一笔（1354-1358）——月上限也被抬，与「仅限今天」的文案不完全一致（见第 3 节）；重启回配置（注释 1340-1342）。
8. 拒绝/超时：非 y 返回 Lan did not approve the higher cost limit (...)（5016-5018）；网页审批 600 秒超时（web_agent.py:111, 1808-1813）返回 timeout，等于未批准。
9. 执行侧硬限：cap = Decimal(limits['run_budget_cny'])（executor_opencode.py:1168）；每轮 cost >= cap 即停，文案「reached the cost limit (¥{cap}); if the job is worth more, run it again with a higher budget_cny and Lan is asked to approve」（executor_opencode.py:1366-1369）；超时「reached the {N}-minute limit」；重复「the same call was repeated more than {n} times」（同段 1365-1371）。
10. 预留：reserve_amount(cfg.id, model, Decimal(run_budget_cny), 'workbench-estimate')（veil_agent.py:5079-5081）；失败返回「The workbench was blocked by the shared cost limit: ...」（5082-5086）。

### 1.4 费用账本 data/costs.jsonl
1. 事件类型：COST_EVENT_TYPES = ('reserve','dispatch','settle')（ledger_recovery.py:17）；check_cost_row 校验 type/id/amount/period（ledger_recovery.py:92-100）。
2. 写入：Store.append 拒绝向未换行的末行追加（veil_agent.py:573-578，报「账本末行不完整，已停止；...」），写一行 JSON 后 fsync（579-583）。
3. reserve 字段：type/id/period/amount/character/model/provider_key/category，可选 accounting（veil_agent.py:1555-1562）。
4. dispatch：mark_dispatched 追加 {'type':'dispatch','id':request_id}（veil_agent.py:1664-1671），用于区分「已发给供应商」与「没发出」。
5. settle：写 amount/prompt_tokens/completion_tokens/cached_tokens/cache_reported/cache_status，可选 category/accounting/input_bytes/usage_status；超界时记 +usage-over-bound 与 bounds（veil_agent.py:1779-1806）。
6. 汇总：_LedgerTotals（veil_agent.py:1243-1315）只增量读新追加行；未结算 reserve 计入 pending（1276-1284）；已结算按 reserve['period'] 的 [:10]/[:7] 归日/月（1282-1283）；同一 id 后写的 settle 覆盖前述金额（1262-1266）。增量不可用时回退全量 _totals_full（1506-1535）。
7. 损坏：未知事件/金额非法/缺字段都抛 UserError，文案「花费账本无法核对，已停止；请保留 data 文件夹，关闭 VEIL 后运行 .venv/Scripts/python.exe data_repair.py diagnose --file data/costs.jsonl 查看。」（veil_agent.py:1504-1505，1499-1501 抛出）。
8. 重启恢复：Budget.__init__ 在数据目录锁内调 recover_interrupted()（1336-1339）；若 ledger_recovery.is_unverified 为真直接返回 0（1693）；估算类 accounting 全额结算，有 dispatch 标记的全额，否则按 0 结算，后缀 -recovered / interrupted-not-sent（1699-1707）。
9. 待核实冻结：_require_verified 在 reserve/estimate/reserve_amount/estimate_model 最前面调用（1538, 1570, 1589, 1609），文案「费用账本有待核实的记录，已暂停所有付费请求，未发送。请先关闭 VEIL，对照供应商账单后运行 .venv/Scripts/python.exe data_repair.py ledger-status / ledger-verify。」（1366-1370）。标记文件 data/costs-unverified.json（ledger_recovery.py:15），证据 costs-unverified.jsonl。
10. 解除：data_repair.py ledger-status / ledger-verify（data_repair.py:155-186）；后者把标记改名存档（ledger_recovery.archive_marker，data_repair.py:186）。

### 1.5 各模块独立预算的接入方式
| 模块 | 是否有独立额度 | 接入点 |
|---|---|---|
| 自主活动 | 有：daily_budget_cny + 单次 run_budget_cny | veil_agent.py:6891；autonomy.py:443, 1313；veil_agent.py:5630-5642 |
| 瞄一眼 | 有：glance.daily_budget_cny | veil_agent.py:6893；autonomy.py:1461, 1661 |
| 经历整理 | 有：memory.experience.daily_budget_cny | experience_memory.py:63, 549, 610, 924 |
| 事件/经历合并 | 复用经历整理日上限（无独立键） | memory_native.py:278-281；event_memory.py:144,152,817 |
| 短期便签 | 无独立额度，仅 category | short_term_notes.py:21-22, 487-488 |
| 想起判官 | 无独立额度，仅 category | recall_judge.py:25-26, 193-213, 329-353 |
| 日历约定 | 无独立额度，仅 category | calendar_agreements.py:71, 79-82 |
| 委派 | 无独立金额上限，固定轮数/工具数/90 秒 | delegate_agent.py:15-16, 20, 46, 57-75 |
| 工作台 | 有：run_budget_cny（自主时 wake_budget_cny） | veil_agent.py:5069-5081；executor_opencode.py:1168 |
| 语音 | 单价来自 .env，按字符算金额 | voice_output.py:53, 136-147 |
| 记忆向量/Ombre | 单价来自 character.toml [memory]，按估算预留 | memory_agent.py:348-402 |
| 上下文记忆 | 非金额，token 预算 | recall_budget.py:35-230 |

### 1.6 优先级链（谁先判）
1. 未核实冻结（_require_verified）最先，直接拒绝一切付费请求（veil_agent.py:1538 等）。
2. 分类上限（_enforce_category_budget，1448-1458）。
3. reserve(category_daily=...) 的显式日上限（1544-1551，仅 event_memory 传入）。
4. 全局「今日已结算 + 全部 pending + 本次预留」对日/月上限（1552-1553）。
5. 进入请求后，自主单次 run_budget_cny 与步数在循环里判（veil_agent.py:5586-5642），属「花到就停」而非「预先拒绝」。
6. 工作台 run_budget_cny 在执行器内部每轮判（executor_opencode.py:1366-1369）。

---

## 2. 配置清单表

> 只有真实存在页面或接口才写入口，否则「仅改文件」；代码常量写「代码写死，无入口」。

### 2.1 全局预算（本机设置 data/<角色>/models.json）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 模型/全局 | 每天上限 limits.daily_cny | model_settings.py:29, 443-446；veil_agent.py:6894 | data/peter/models.json → limits.daily_cny（字符串） | 10（DEFAULT_DAILY） | 数字，>0，≤1000，元；且必须 ≤ 月上限 | 设置页「模型 → 费用上限 每天 ¥」desktop/chat.html:199 → desktop/chat.js:2806,2857 → POST /api/models（web_agent.py:2825） | models.json 覆盖默认值；内存 budget.daily 由 limits() 覆盖（web_agent.py:3713） | 保存后立即（下一次 reserve；web_agent.py:1523-1531） | 是（models.json；备份含 data/，backup_restore.py:23） | 直接决定钱；无二次确认，保存即生效 | U |
| 模型/全局 | 每月上限 limits.monthly_cny | model_settings.py:30, 445-449 | 同上 limits.monthly_cny | 300（DEFAULT_MONTHLY） | 数字，>0，≤30000，元；不得小于日上限 | 设置页「每月 ¥」desktop/chat.html:199 → chat.js:2857 | 同上 | 保存后立即 | 是 | 同上 | U |
| 预算 | 运行期日上限 Budget.daily | veil_agent.py:1328；web_agent.py:3713 | 进程内存 | 10（构造参数） | Decimal 元 | 无入口（只由 models.json 推导） | 由 models.limits() 覆盖 | 启动/保存 | 否 | 两个默认值（model_settings.py:29 与 veil_agent.py:1328）必须同步，否则行为分叉 | R |
| 预算 | 运行期月上限 Budget.monthly | veil_agent.py:1329；web_agent.py:3713 | 进程内存 | 300 | Decimal 元 | 无入口 | 同上 | 启动/保存 | 否 | 同上 | R |
| 预算 | 验收实例硬上限 | web_agent.py:3715-3719 | 代码写死 | 2 / 20（元） | 固定 | 代码写死，无入口 | 对配置值取 min，仅 --acceptance | 启动 | 否 | 防止验收实例跑出真钱 | P |
| 预算 | 临时提额 _raise | veil_agent.py:1343-1358 | 进程内存 (YYYY-MM-DD, Decimal) | 0 | 任意正数；来源受 50 元申请上限约束 | 只能由工作台审批卡触发（veil_agent.py:5019-5020） | 加在 daily_limit() 与 monthly_limit() 两处 | 当次进程立即 | 否（重启回落配置，注释 1340-1342） | 等价于一次授权提额；同时抬高月上限（1357-1358） | S |
| 预算 | 单次申请合理性上限 MAX_RUN_BUDGET_REQUEST | veil_agent.py:1324 | 代码常量 | 50（元） | 固定 | 代码写死，无入口 | 在 _approve_run_budget 校验（5002-5003） | 即时 | 否 | 注释原文 the real limit is Lan's yes on the card | P |

### 2.2 分类/模块预算（角色配置 characters/peter/character.toml 与页面开关）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 自主活动 | [autonomy] daily_budget_cny | character.toml:141；veil_agent.py:1020, 6891；autonomy.py:443 | character.toml（TOML 字符串） | 2（veil_agent.py:1020） | price>0 的有限数，元 | 仅改文件 | 覆盖 Budget.autonomy_daily 构造默认 2（veil_agent.py:1328-1336） | 下次启动（控制器 443 只读一次） | 随仓库（tracked） | 写错会让自主活动整体不可用；非法值报「角色配置无法读取或字段无效」（veil_agent.py:1197-1199） | A |
| 自主活动 | [autonomy] run_budget_cny | character.toml:149；veil_agent.py:1029-1040 | character.toml | 无（缺省不按钱停） | price>0，元 | 仅改文件 | 设置后启用「花到即停」，并让 max_tool_steps 生效（1039-1041） | 下次自主活动 | 随仓库 | 与每日池独立 | A |
| 自主活动 | [autonomy] max_tool_steps | character.toml:150；veil_agent.py:1041；autonomy.py:1354 | character.toml | 60（仅当 run_budget_cny 存在；否则回落 [tools] max_steps） | 正整数 | 仅改文件 | 只有 run_budget_cny 存在才被读取（1039-1041） | 下次自主活动 | 随仓库 | 只设它不设 run_budget 时是死键（见第 3 节） | A |
| 自主活动 | interval_minutes / daily_max_runs | character.toml:139-140；veil_agent.py:1017-1018 | character.toml | 180 / 6 | 正整数，分钟 / 次 | 仅改文件 | 无 | 下次启动 | 随仓库 | 影响频率，间接影响花费 | A |
| 自主活动 | max_input_tokens | character.toml:147；veil_agent.py:1027-1038 | character.toml | 无（缺省用模型上限） | 正整数 | 仅改文件 | 覆盖请求输入窗口 | 下次请求 | 随仓库 | 抬高预留金额 | A |
| 瞄一眼 | [glance] daily_budget_cny | character.toml:161；veil_agent.py:1050, 6893；autonomy.py:1461 | character.toml | 1 | price>0，元 | 仅改文件 | 覆盖 Budget.glance_daily（veil_agent.py:1337） | 下次启动 | 随仓库 | 频次由 min/max interval 控制 | A |
| 瞄一眼 | max_steps | character.toml:160；veil_agent.py:1053-1054；autonomy.py:1461 | character.toml | 4 | 整数且 ≤4（1054 校验） | 仅改文件 | 与 [tools] max_steps 取 min（veil_agent.py:5590-5591） | 下次启动 | 随仓库 | 上限被代码封死 4 | A |
| 瞄一眼 | min_interval_minutes / max_interval_minutes / idle_seconds | character.toml:157-159；veil_agent.py:1051-1052, 1056 | character.toml | 15 / 40 / 300 | 正整数；max≥min（1056） | 仅改文件 | 无 | 下次启动 | 随仓库 | 影响触发频率与花费 | A |
| 经历整理 | [memory.experience] daily_budget_cny | character.toml:96；experience_memory.py:63, 97-101 | character.toml | 1（Decimal('1')） | 数字，≥0（0 = 停用付费整理） | 仅改文件（记忆页只显示，chat.js:938） | 功能自判在全局预算之前（549, 610, 924） | 下次启动/下次整理 | 随仓库 | 0 时不发付费请求 | A |
| 经历整理 | [memory.experience] enabled | character.toml:90；experience_memory.py:63, 116-125 | character.toml 默认；data/peter/experience-memory.json 覆盖 | False | bool | 记忆页开关 → POST /api/experience-memory（web_agent.py:3151-3164；chat.js:938） | 页面 json 覆盖 toml（enabled() 先读 json） | 即时 | 是（json） | 关掉只是不整理，不删数据 | U |
| 想起判官 | [memory.recall_judge] enabled / timeout_seconds / ombre_candidates / vector_candidates / breaker_failures / breaker_seconds | recall_judge.py:36-40, 42-52 | character.toml 可选表 | enabled=true, timeout=4.0, 8, 8, 3, 300 | timeout 0.5-10 秒；候选 0-16；breaker 1-20 次；5-3600 秒；非法值回落默认（42-52） | 记忆页开关 → POST /api/recall-judge（web_agent.py:3165-3174） | 页面开关即时；数值仅文件 | 开关即时；数值下次启动 | 开关持久化位置未确认（见第 7 节） | 判官失败回落旧召回，不阻断聊天 | A |
| 短期便签 | [short_term_notes] enabled | character.toml:234-237 | character.toml | false（仓库值） | bool | 仅改文件 | 启动调度开关 | 下次启动 | 随仓库 | 默认关，避免无收益花费 | A |
| 短期便签 | 提取成本（无独立额度） | short_term_notes.py:21-22, 487-508 | 无 | 无 | 元（继承模型价） | 无入口 | 占 short_term_notes 类目 | 每次提取 | — | 单次上限仅 MAX_REQUEST_BYTES=20000（short_term_notes.py:20） | X |
| 日历约定 | 判断成本（无独立额度） | calendar_agreements.py:71-82 | 无 | 无 | 元（继承模型价） | 无入口；受 life_records.auto_record_enabled() 总开关（41-42） | 占 calendar_agreements 类目 | 每轮命中时 | — | 失败写 jsonl 但静默 | X |
| 委派 | MAX_TURNS / MAX_TOOL_CALLS / 90 秒 / 1200 tokens | delegate_agent.py:15-16, 20, 46, 64 | 代码常量 | 6 / 12 / 90s / 1200 | 固定 | 代码写死，无入口 | 无独立金额上限，只占 delegate 类目 | 每次委派 | 否 | deadline = started + 90（46）硬编码 | P |
| 工作台 | [executor] run_budget_cny | character.toml 当前无此行；veil_agent.py:1142；executor_opencode.py:40 | character.toml 或代码默认 | '0.5' | price>0，元 | 仅改文件 | 自主活动时被 wake_budget_cny 取 min（veil_agent.py:5071） | 下次任务 | 随仓库 | 决定单次任务上限 | A |
| 工作台 | [executor] wake_budget_cny | veil_agent.py:1143 | character.toml 或代码默认 | '0.3' | price>0，元 | 仅改文件 | 覆盖自主活动时的单次上限 | 下次任务 | 随仓库 | 同上 | A |
| 工作台 | max_seconds / repeat_limit | character.toml 当前无；veil_agent.py:1140-1141；executor_opencode.py:40 | character.toml 或代码默认 | 600 秒 / 3 次 | 正整数 | 仅改文件 | 与金额上限一起判 | 下次任务 | 随仓库 | 注释原文 The cost cap is the hard limit; the time and repeat limits only catch a stuck run（executor_opencode.py:39） | A |
| 工作台 | 代理请求数上限 PROXY_MAX_REQUESTS | executor_opencode.py:41, 1169 | 代码常量 | 600 | 固定 | 代码写死，无入口 | 无 | 每次任务 | 否 | 注释 runaway guard for the API key, not a task limit | P |
| 工作台 | 单次申请提额 | veil_agent.py:4994-5021 | 内存 + 当次 limits | 无 | >0 且 ≤50 元 | 只能由 Peter 调 workbench(task, budget_cny=...)（4833-4841） | 先于预留；自主活动忽略（5072） | 单次任务 | 否 | 需 Lan 在卡上同意；超时=拒绝 | S |

### 2.3 价格、账本与非金额预算

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 模型价格 | cny_per_call / 输入 / 缓存 / 输出每百万 | character.toml:20-22,32-34；model_settings.py:406-421 | character.toml [providers.*]；models.json models[] | 按界面模型表单（chat.html:216-220） | 每次 ≤100 元；每百万 ≤10000 元；缓存 ≤ 输入（model_settings.py:413-421） | 设置页「模型」表单 → /api/models | 选中的模型决定实际价 | 下一请求 | 是 | 抄错价会让账本与真实账单不符 | U |
| 模型 | max_input_tokens | character.toml:17,30；model_settings.py:31, 424-431 | toml / models.json | 32768（DEFAULT_MAX_INPUT） | 4096–2,000,000 tokens | 设置页「最长输入（tokens）」chat.html:220 | 影响预留上限与请求裁切 | 下一请求 | 是 | 设太大 → 每次预留变大，更容易触顶 | A |
| 记忆向量 | [memory] input_cny_per_million | character.toml:45；memory_agent.py:357-360；recall_judge.py:198 | character.toml | 0（当前仓库值） | price 校验（veil_agent.py:768-770） | 仅改文件 | 用于向量估算预留 | 下次启动 | 随仓库 | 改成收费模型后会开始花钱 | A |
| 语音 | MINIMAX_CNY_PER_10K_CHARS | voice_output.py:53；.env.example:8 | .env | 3.5（元/万字） | 有限且 >0，否则语音功能整体隐藏（53-58） | 仅改 .env | 无 | 下次启动 | 是（备份含 .env，backup_restore.py:23） | 明文密钥同文件，属敏感 .env | S |
| 账本 | data/costs.jsonl 事件流 | veil_agent.py:1243-1315, 1537-1806；ledger_recovery.py:17, 92-100 | data/costs.jsonl（追加 JSONL；行内 time 由 Store.append 自动加，veil_agent.py:579） | 无（空账本=0 花费） | reserve/dispatch/settle 三型；amount 非负有限；period 必须真实 YYYY-MM-DD | 无入口（只能用 data_repair.py 诊断/修复） | 所有预算判定的唯一事实来源 | 每事件即时 fsync | 是 | 损坏会冻结全部付费请求 | R |
| 账本 | 未核实冻结标记 | veil_agent.py:1366-1370, 1686-1694；ledger_recovery.py:15, 216, 277 | data/costs-unverified.json + data/costs-unverified.jsonl | 无 | 文件存在即冻结 | 只能 data_repair.py ledger-status / ledger-verify（155-186） | 优先级最高，先于一切 | 启动/每请求 | 是；恢复时强制带入（backup_restore.py:580-582） | 解除需人工核对供应商账单 | P |
| 账本 | 恢复时对账 | backup_restore.py:572-624 | 备份/恢复流程 | — | 冲突行进证据文件并冻结（610-624） | 无页面 | 恢复后覆盖目标账本 | 恢复时 | 是 | 不会把未知花费当 0 | P |
| 展示 | /cost 文本 | veil_agent.py:6804-6807；display 1838-1861 | 计算 | — | — | 聊天页「花费」按钮（chat.html:260；chat.js:2628）或手输 /cost | 只读 | 即时 | — | 无 | R |
| 展示 | 按模型花费 | veil_agent.py:1865-1883 | 计算 | — | — | 同 /cost | 只读 | 即时 | — | 只统计当天已结算 | R |
| 展示 | 缓存命中率 | veil_agent.py:1793-1836 | 计算 | — | — | 同 /cost | 只读 | 即时 | — | 未报告缓存的请求单列 | R |
| 展示 | 诊断页预算行 | diagnostics.py:615-628 | 计算 | — | — | 诊断页 | 只读 | 即时 | — | 只显示自主活动一类 | R |
| 上下文 | 记忆 token 预算（非金额） | recall_budget.py:35-230 | 内存 | ratio 兜底 0.4（40-45） | 0.15–1.0（43-45） | 无入口（自动） | 以 provider.max_input_tokens 为总空间 | 每请求 | 否 | 只影响上下文，不影响钱 | P |
| 提示 | autonomy.budget.line 文案 | prompts/registry.py:245-247；prompts/defaults/autonomy.budget.line.md | 提示词库（可覆盖） | 见默认文件 | max_bytes=600 | 无入口（源码/提示文件） | 只改文字，数字由 autonomy.py:1223 填 | 下次自主活动 | 是（提示文件） | 改错文案会让模型误读预算 | X |

---

## 3. 重复 / 冲突 / 隐性覆盖

1. 同一「每日上限」有两个默认值：DEFAULT_DAILY=10（model_settings.py:29）与 Budget(daily=Decimal('10'))（veil_agent.py:1328）。models.json 缺失时一致；单改一处就分叉。
2. 临时提额同时抬高月上限：ticket 340 文案是「今天的总上限……仅限今天」（veil_agent.py:5010-5012；docs/tickets/340-budget-raise-request.md），但 monthly_limit() 也加 _raised()（1357-1358）。本月上限在进程内被放大，/cost 会显示更高的月上限。
3. 提额不持久，且只对下一次 reserve 生效：内存字段（1343-1349），重启回配置；/cost 显示的是加上提额后的数（1838-1840）。
4. pending 不分日期：reserve 用 day + pending + amount（1552），而 pending 是所有未结算预留之和（1276-1277）；_totals_full 注释原文 Uncertain requests remain reserved, including after midnight.（1524-1525）。跨天后昨天的挂起仍占今天额度。
5. 两套分类拒绝文案：_enforce_category_budget 抛「{label}已达到 ¥{cap}/日上限，聊天不受影响。」（1457）；reserve(category_daily=...) 抛「{category} 已达到 ¥{cap}/日上限，未发送。」（1551）。同一类目可能被两处拒绝，措辞不一致。
6. 显示分类与实际 category 不一致：display() 的「聊天」是 day 减去所有列出的类目（1844-1846），而语音（voice_output.py:137）、向量估算（veil_agent.py:1567-1581）、工作台（veil_agent.py:5079）都落在 category='chat'（_reserve_category，1444-1446），因此「聊天 ¥x」里混着语音/向量/工作台。
7. 自主活动忽略 Peter 的 budget_cny 申请：if budget_cny is not None and not autonomy（5072），自主活动只能用 wake_budget_cny；审批卡在自主活动里禁弹（_capture_approval，autonomy.py:1131-1137）。
8. max_tool_steps 是条件键：只有 run_budget_cny 存在时才写入 autonomy（veil_agent.py:1039-1041）；只写 max_tool_steps 不生效，回落到 [tools] max_steps（123）。运行时记录上限也按同一条件（autonomy.py:1354-1355）。
9. [tools] max_steps 被两处取 min：普通请求用 cfg.tools.max_steps（veil_agent.py:123, 5580）；瞄一眼再与 glance.max_steps 取 min（5590-5591）。
10. 工作台开关在 data/，额度在 character.toml：workbench.json 只允许 enabled/project_roots/container_image/web_hosts（veil_agent.py:798, 814-829），改不了 run_budget_cny——用户以为「工作台设置」能调钱，其实不能。
11. [memory.events] 没有独立预算键：复用 [memory.experience] daily_budget_cny（memory_native.py:278-281；event_memory.py:152, 817），但开关独立（character.toml:100-104）。关掉经历整理不会关事件合并，事件合并仍花同一笔日上限。
12. short_term_notes 只有类目、没有额度：short_term_notes.py:21-22, 487-488；默认 enabled=false（character.toml:237）。只受全局日/月上限约束。
13. 日历约定同样无独立额度：calendar_agreements.py:71, 79-82，只在 display() 里出现一行（1852）。
14. 判官与向量请求各自预留：_embed 用 budget.estimate（recall_judge.py:201），_ask 用 budget.reserve（335）；都算全局日/月，但一个记 embedding-estimate、一个记 recall-judge，embedding 在「聊天」口径里（见第 6 条）。
15. 文案可被提示词覆盖：autonomy.budget.line 的金额来自代码（autonomy.py:1223），文字来自 prompts/defaults/autonomy.budget.line.md（可覆盖，registry.py:245-247）；数字与措辞可能脱节。
16. BudgetExceeded 被多处吞掉：聊天循环（veil_agent.py:6852-6859 继续会话）、工具循环（6067-6076 把工具标「被拒绝」并输出「The tool-step limit for this request has been reached; continuing would exceed the current spending limit.」6088-6089）、语音（3078-3080 降级成「语音生成失败」仍发文字）、自主/瞄一眼（autonomy.py:1365-1368, 1669-1670）、codex_watch（codex_watch.py:429）。同一错误在不同入口表现差异很大。
17. 工作台 budget_cny 只抬「本次上限」，预留却取 limits['run_budget_cny']（veil_agent.py:5020, 5079-5080）：提额额度是 shortfall（相对日/月剩余），两者口径不同，批准后仍可能被并发预留挤占后拒绝。
18. 条件生效的易误配项：[executor] wake_budget_cny 只在自主活动下生效（5071），普通工作台任务改它没有任何效果。

---

## 4. 页面与入口现状

已有页面/接口（可改预算相关值）
- 设置页 → 模型卡片：
  - 「费用上限 每天 ¥ / 每月 ¥」+ 保存：desktop/chat.html:199；回填 desktop/chat.js:2806-2807；保存 chat.js:2857；POST /api/models（web_agent.py:2825 → _post_models 2823-2839 → hub.save_models 1518-1537）；保存后立即更新 budget.daily/monthly（1523-1531，含 acceptance 夹取）。
  - 模型单价、最长输入：chat.html:216-220；保存走同一接口（model_settings.py:406-431）。
- 记忆页：
  - 「经历整理」开关 → POST /api/experience-memory（web_agent.py:3151-3164）；状态含 spent_today / daily_budget_cny（experience_memory.py:1104-1128），前端 chat.js:918, 938。
  - 「想起判官」开关 → POST /api/recall-judge（web_agent.py:3165-3174）；状态含 spent_today（recall_judge.py:436-444）。
- 聊天页：
  - 「花费」按钮发 /cost（chat.html:260；chat.js:2628）；由命令行同款循环处理（veil_agent.py:6804-6807；web 也跑 run_chat，web_agent.py:3641）。
  - 审批卡（/api/approve，web_agent.py:3411）：工作台提额走这张卡（web_agent.py:1799-1823），600 秒超时（111, 1808-1813）。
- 诊断页：只有一行只读预算合计（diagnostics.py:615-628），无编辑。

仅改文件（无页面）
- characters/peter/character.toml：[autonomy] daily_budget_cny / run_budget_cny / max_tool_steps / interval_minutes / daily_max_runs / max_input_tokens（137-150）；[glance] daily_budget_cny / max_steps / min|max_interval_minutes / idle_seconds（155-161）；[memory.experience] daily_budget_cny（96）；[memory.recall_judge] 数值项；[executor] run_budget_cny / wake_budget_cny / max_seconds / repeat_limit；[tools] max_steps / max_output_tokens（117-123）。
- .env：MINIMAX_CNY_PER_10K_CHARS（.env.example:8）。
- data/peter/models.json：可直接编辑，但会被 ModelSettings.save 的整表替换覆盖（model_settings.py:617 起）。

代码写死，无入口
- MAX_RUN_BUDGET_REQUEST=50（veil_agent.py:1324）；APPROVAL_WAIT_SECONDS=600（web_agent.py:111）；PROXY_MAX_REQUESTS=600（executor_opencode.py:41）；STALE_GLANCE_SECONDS=600（veil_agent.py:1460）；MAX_RUNTIME_SECONDS=15*60（autonomy.py:17）；委派 6 轮/12 次/90 秒/1200 tokens（delegate_agent.py:15-16,20,46）；MAX_OUTPUT_TOKENS 见 recall_judge.py:27、short_term_notes.py:19、experience_memory.py:43；验收上限 2/20（web_agent.py:3715-3719）；MESSAGE_FRAMING_BYTES=16 与 ratio 夹取（recall_budget.py:35,43-45）。

没有的东西
- 没有任何「预算中心/费用页面」：设置页只有一行日报/月报输入框，没有当日已花、剩余额度、账本状态、冻结解除入口。
- 诊断页不显示 pending、不显示账本冻结；冻结只能靠 data_repair.py（命令行）。
- 页面上看不到 autonomy/glance/experience/executor 的数值，只能看到经历整理「今天花了多少 / 上限」（chat.js:938）。

---

## 5. 生效时机与持久化

| 变更 | 生效时机 | 重启后 | 更新后 | 备份/恢复后 |
|---|---|---|---|---|
| 设置页日/月上限（/api/models） | 保存即改内存 budget.daily/monthly（web_agent.py:1523-1529），下一请求生效 | 保留（models.json） | 保留（data/ 不在仓库；update.bat:3 明示 never touches data） | 保留（备份含 data/，backup_restore.py:23） |
| raise_today 临时提额 | 立即影响 daily_limit/monthly_limit | 丢失（内存） | 丢失 | 丢失 |
| character.toml 的 autonomy/glance/executor/experience 额度 | 下次启动（控制器 443；Budget 6891-6893 只读一次） | 保留（文件） | 取决于 git：update.bat 执行 git pull --ff-only origin main（update.bat:58）；若上游也改了同一文件而本地有未提交改动，按 git 语义该 pull 会中止（本机未实测） | 保留（characters/ 在备份根，backup_restore.py:23） |
| data/peter/workbench.json 工作台开关 | 下次启动（load_character 合并，veil_agent.py:1137-1139） | 保留 | 保留（data/ 被忽略，.gitignore:11） | 保留 |
| 经历整理/判官页面开关 | 即时 | 保留（json；判官见第 7 节） | 保留 | 保留 |
| .env 语音单价 | 下次启动（load_voice_config，voice_output.py:46-59） | 保留 | 保留（.env 被忽略 .gitignore:2；备份含 .env） | 保留 |
| data/costs.jsonl | 每事件追加并 fsync（veil_agent.py:579-583） | 保留 | 保留 | 恢复时与恢复前账本对账（backup_restore.py:572-624），冲突则冻结 |
| 未核实冻结 | 标记文件存在即生效（ledger_recovery.py:216） | 保留 | 保留 | 强制带入（backup_restore.py:580-582） |

跨天恢复：已结算花费按 reserve 的 period 归日（veil_agent.py:1282-1283），新的一天从 0 计；未结算挂起跨天仍占额度（1524-1525）；_raised 只在当天有效（1346-1349）；自主单次花费跨天从 0 重算（veil_agent.py:5636-5638）。

重启恢复：Budget.__init__ → recover_interrupted()（1336-1339）：估算类与已 dispatch 的预留按上限结算为花费，未 dispatch 的按 0 释放（1695-1707）；账本 unverified 时不做任何结算（1693）。

损坏/待核实恢复：未知事件或非法金额让 totals() 抛 UserError（1496-1501, 1529-1534）；未核实标记只能人工核对后用 data_repair.py ledger-verify 解除（data_repair.py:172-190）。

---

## 6. 风险与建议分类

- U（建议直接做成页面可改）
  - 全局每天/每月上限：已有页面，建议补「今日已花、剩余、pending、账本状态」与保存前提示（当前 chat.html:199 只有两个数字框）。
  - 经历整理开关、想起判官开关：已有开关，可在同一「费用」页聚合。
  - 语音单价 MINIMAX_CNY_PER_10K_CHARS：属价格而非密钥，可做成高级项（但同文件含密钥，见 S）。
- A（高级设置，需说明影响）
  - autonomy.daily_budget_cny、run_budget_cny、max_tool_steps（注意 1039-1041 的条件依赖，页面应把两者绑定显示）。
  - glance.daily_budget_cny、max_steps（代码封顶 4）。
  - memory.experience.daily_budget_cny（0 = 停用付费整理）。
  - executor.run_budget_cny、wake_budget_cny、max_seconds、repeat_limit；以及「为什么 workbench.json 改不了额度」（798, 814-829）。
  - 模型单价与 max_input_tokens（直接影响预留金额，见 1539-1542）。
- S（敏感：需审批/授权）
  - 临时提额（raise_today）与工作台 budget_cny：必须保留 Lan 的审批卡，不能做成「一键提额」（ticket 340；veil_agent.py:5013-5020）；注意月上限也被抬（1357-1358），页面应显式写出。
  - .env 的语音价格与密钥同文件：页面改价可能顺带暴露密钥字段，建议只读展示 + 批准后写回。
  - 解除账本冻结（ledger-verify）：属人工核对，不应给 Peter 任何工具入口。
- P（程序安全约束，保持底层强制）
  - 未核实冻结与账本校验（1366-1370；ledger_recovery.py:92-100）。
  - 预留是硬上限、结算不得超过预留（1629-1638 注释与校验）。
  - MAX_RUN_BUDGET_REQUEST=50 只是防写错，真正把关是审批（1324 注释）。
  - 验收实例 2/20 上限、PROXY_MAX_REQUESTS、委派 90 秒、瞄一眼 4 步上限。
- R（只读运行信息）
  - /cost 全部输出、cache_display、model_display、诊断页预算行；建议做成只读「今日账本」卡片。
- X（冗余/废弃，给清理依据）
  - autonomy.max_tool_steps 与 [tools] max_steps 的双入口（1039-1041 条件生效）——建议收敛或显式标注。
  - [memory.events] 无独立预算键（复用 experience），建议页面标注「共用同一日上限」。
  - short_term_notes、calendar_agreements 有类目无额度：要么给独立额度，要么明确「只受全局上限约束」。
  - display() 的「聊天」口径（1844-1846）建议改为显式「其它/未分类」，避免语音/向量/工作台被算进聊天。

---

## 7. 无法确认项（明确原因，不猜）

1. 当前实际生效值：正式运行目录的 data/peter/models.json、data/costs.jsonl、data/peter/workbench.json、data/peter/experience-memory.json、.env 均未读取（任务禁止读正式目录/运行数据）。所以「今天还剩多少、上限现在是多少、语音单价是多少」无法读取（原因：只读边界）。
2. 判官开关的持久化位置：只读到 judge.set_enabled(...) 的调用（web_agent.py:3170）与 status()（recall_judge.py:436-444），未读 set_enabled 内部，无法确认写 json、toml 还是内存（原因：时间用尽，未读该函数）。
3. 短期便签调度是否真的启动：只确认 [short_term_notes] enabled=false（character.toml:237）与 ShortTermNotes.start()（short_term_notes.py:121-126），未追到 Chat 里决定是否启动的判断点（原因：未读到启动分支）。
4. update.bat 对本地 character.toml 修改的确切结果：只确认执行 git pull --ff-only origin main（update.bat:58）且注释称 never touches data or persona（update.bat:3）；对 tracked 的 character.toml 有本地未提交改动时，按 git 语义 pull 会中止，但未在本机实测（原因：不可在正式目录执行 git）。
5. data_repair.py ledger-status 是否能被页面调用：只确认命令行入口（data_repair.py:197-213），按路由名 grep 未见 web_agent.py 对应路由，但未逐条核对所有隐藏路由（原因：路由表很长，仅按路径名 grep）。
6. recall_budget 的 calibration 来源：只确认 ratio() 兜底 0.4、夹取 0.15–1.0（recall_budget.py:38-45），未追到 calibration 从哪个配置/统计值传入（原因：未读调用方拼装段）。
7. 是否存在其它预算类配置键：按 budget/cny/cost/limit 在 *.py、desktop/*.js、desktop/*.html、character.toml 做了关键词取证；未覆盖 docs、scripts、desktop/main.cjs 等（原因：时间用尽，优先保证核心链）。
8. prompts 覆盖文件的实际位置：只确认 autonomy.budget.line 在 prompts/defaults/ 且有 registry 登记（registry.py:245-247），未确认用户覆盖是否写在 data/<角色>/prompts（原因：未读 prompts/store.py）。

---

### 附：本次取证的一手依据（便于复核）
- model_settings.py:29-30, 36-52, 406-431, 443-449, 568-573, 601-613
- veil_agent.py:1243-1315, 1324-1394, 1444-1505, 1537-1806, 1838-1883, 4493, 4994-5021, 5069-5081, 5586-5591, 5630-5642, 6067-6091, 6804-6807, 6852-6859, 6889-6894
- web_agent.py:111, 1518-1537, 1799-1823, 2823-2839, 3151-3174, 3641, 3702-3719
- autonomy.py:17, 443, 1017, 1145, 1223, 1288, 1339-1356, 1461, 1544-1555, 1661-1670
- executor_opencode.py:39-42, 67, 1168, 1365-1371
- experience_memory.py:43-66, 84-101, 549-550, 610-611, 887, 924, 1104-1128
- recall_judge.py:25-27, 36-52, 193-213, 329-353, 436-444
- calendar_agreements.py:41-42, 71-82；short_term_notes.py:15-22, 487-508
- delegate_agent.py:15-16, 20, 46, 57-75；voice_output.py:46-59, 135-147
- ledger_recovery.py:15-17, 92-100, 153-200, 216, 277；data_repair.py:155-190
- backup_restore.py:23, 572-624；diagnostics.py:615-628；recall_budget.py:35-45, 88-190
- characters/peter/character.toml:7, 17-22, 30-34, 45, 89-96, 100-104, 116-123, 137-150, 155-161, 234-237
- desktop/chat.html:199, 216-220, 260；desktop/chat.js:918, 938, 2628, 2806-2807, 2857
- .env.example:8；update.bat:3, 58；docs/tickets/340-budget-raise-request.md
