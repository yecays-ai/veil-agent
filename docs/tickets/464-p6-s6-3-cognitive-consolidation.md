# 工单 464 · P6-S6-3 后台认知巩固与经验提炼

> 状态：开发、回归测试与 VEIL Lab 隔离验证完成；待提交 PR、CI、独立审查与 Lan 体验。**默认关闭（`enabled=False`）供灰度体验；未合并、未部署，未触碰正式 `data/peter`。**
> 所属格：**A（陪伴核心 · 记忆与认知持续成长闭环）**
> 施工依据：PR #455 `AB-E1` 实验与决策表（`docs/reports/p6-s4-ablations-20261126.md`、`docs/reports/p6-s4-decision-table-20261126.md`、`docs/reports/p6-s4-requirements-coverage-audit-20261126.md`），衔接已合入的 S3（PR #460）、S5（PR #461）、S6 开工门禁（PR #462）、S6-2（PR #463）与 S6-1（PR #464，`90846bd`）。

## 1. 三问

1. **为什么做**：此前 Peter 的学习主要停留在单次对话即时抽取或局部便签整理，缺少跨多段共同经历与自主活动的定期综合归纳能力，也无法在后续新经历与旧认知冲突时自动提出带证据的演进修订，更缺少面向 P7 经验影响行为层的统一结构化沉淀入口。
2. **做什么**：实现后台认知巩固与经验提炼引擎 `veil/brain/cognitive_consolidation.py`，定期从新经历、相关历史锚点、经过审计的自主活动及当前 Learned Context 中提炼新认知、冲突修订建议、S6-1 重要度重评建议与 P7 情境经验候选；全部认知与重要度写入严格复用 S3 与 S6-1 现有治理链路，不另建平行系统；接入 Brain 与费用管理界面，默认关闭。
3. **不做什么**：
   - 不另建第二套认知存储或绕过 S3 的锁定、更正、否决与四区预算规则；
   - 不把未确认的 P7 情境经验直接塞入 `HEAD` 或静态系统提示词；
   - 不擅自追加付费 live 模型实验，不触碰正式 `data/peter`，不自行合并或部署；
   - 不在本单修改 S5 延期的隐式工作 `trap` 穿透与同轮判官联合门控（明确保持未关闭状态，避免越界）。

## 2. 成熟开源模式调研与复用原则（`reuse-first`）

开工前对照主流开源记忆巩固实现与本仓既有架构做过取舍：

| 开源参考 | 核心模式 | 本单采纳点 | 本单拒绝点（原因） |
| --- | --- | --- | --- |
| **Stanford Generative Agents** (Park et al.) | Reflection：累积重要度触发高阶洞察沉淀，保留指向原始记忆的证据指针 | 跨多段经历综合抽象 + 强制保留 `evidence_refs` 与 `reason` | 不另建独立 Reflection 树库，全部回流 S3 `LearnedContext` |
| **Mem0 / MemGPT (Letta)** | 增量提取 + 对比既有认知做 `ADD / UPDATE / NOOP` 演进，避免重复堆积 | 游标增量 + 批次指纹去重 + `new / revision` 对比演进 + 重复相似度拦截 | 不允许模型静默覆盖用户确认/锁定认知，改为全部走 S3 主权与版本链 |
| **Hindsight / 神经科学慢巩固** | 离线慢波巩固 + 情境锚点关联 + 反自证隔离 | 新旧记忆“近因+价值”均衡选批 + 关联历史锚点 + 过滤 S6-2 不洁自主输出 + 自主自猜不得晋升关系底座 | 不在聊天主线程同步执行，独立低频调度 + 独立日预算上限 |

本仓优先复用清单：
- **认知存储与治理**：100% 复用 `veil/brain/learned_context.py`（`submit_candidate`、`reverify_relationship_evidence`、四区预算、版本链、锁定/更正/否决）与 `veil/brain/relationship_semantic.py`（语义核验排队）；
- **重要度重评**：100% 复用 S6-1 `veil/brain/memory_weights.py` 的 `MemoryWeights.propose()`（含版本、锁定/置顶保护、撤回与同事务删除）；
- **自主经历来源**：直接读取 S6-2 审计后的 `autonomy.jsonl`（过滤 `output_audit.clean is False` 的旁白/扮演记录，关联 `leisure_association.picked`）；
- **后台调度与费用**：复用 `PeriodicBackgroundTick` 与共享 `Budget` 账本（独立类目 `cognitive_consolidation`）；
- **配置与界面**：复用 `config_registry.py`、`brain_p4_api.py`、`desktop/workspace-brain.js` 与 `desktop/workspace-budget.js`。

## 3. 架构设计与关键机制

### 3.1 后台巩固流水线（`veil/brain/cognitive_consolidation.py`）
1. **增量选批与跨日连续性**：
   - 状态持久化于 `data/<role>/cognitive-consolidation-state.json`（已处理记忆指纹、已处理自主记录指纹、已处理批次键、每日运行计数、退避状态、在途事务 `in_flight`、P7 经验候选列表、S6-1 提权去重账本）；
   - 新经历采用 **近因 + 认知价值均衡配额**（至少一半配额留给最新未处理记忆，其余按重要度/事件阶段/个人意义/用户原话/送达次数排序），避免成熟大库中大量历史高重要度记忆饿死当天新经历；
   - 按 `relations`、词法重合度与 `leisure_association.picked` 选出相关历史锚点，连同当前 `facts` / `growth` / `relationship` / `ongoing` 与现有 P7 候选一并送入分析；
   - UTF-8 字节自适应裁剪（`_fit_messages`）与预算感知输入上限（`MAX_INPUT_TOKENS=16000`、`MAX_OUTPUT_TOKENS=1400`），避免中文 3 字节展开触发超限或保守预留超过默认 `¥0.05/日` 上限。
2. **认知演进与 S3 治理**：
   - 系统提示词注册于 `veil/prompts/defaults/memory.consolidation.prompt.md`（`memory.consolidation.prompt`，不可编辑系统提示词）；
   - 严格 JSON 输出校验：每条 `cognition_updates` / `importance_proposals` / `experience_candidates` 的 `evidence_refs` 必须命中本批真实存在的引用集合，任何幻觉引用一律拒收计数（`rejected_ungrounded`）；
   - 与现有认知高度重复的条目直接跳过（`skipped_duplicates`）；
   - `relationship` 写入前展开并仅保留可核验的用户原话/事件来源（防止混入无链指针拖死 S3 语义核验）；仅含自主活动而无真实用户证据的条目强制降级为 `uncertain`（`source='peter'`），严禁循环自证或自主自猜晋升；
   - `revision` 修订先校验锁定与用户主权（锁定项返回 `blocked_sovereignty`，零修改），允许修订的条目经 S3 提交/语义核验后自动将旧条目退为 `outdated` 并写入 `superseded_by` 保留完整历史版本。
3. **S6-1 重要度重评协作**：
   - 统一经 `MemoryWeights.propose(..., source='cognitive_consolidation', evidence_refs=...)` 提交；
   - 内置去重与拒绝尊重：同一 `(memory_id, proposed_importance)` 在证据集合未新增或曾被 Lan 拒绝时不再重复提权；
   - 遵循 `auto_apply_importance`（默认关闭，仅提交待审；开启后仍受 S6-1 硬边界约束：自动仅允许 `+1` 且上限 `7`，降权、`8+`、锁定、置顶一律留在 `pending` 待审）。
4. **P7 情境经验预留接口（`p7.experience.v1`）**：
   - 结构化字段：`id`、`schema_version`、`kind`（`behavioral_pattern` / `tool_lesson` / `communication_pattern` / `autonomy_lesson`）、`trigger_condition`、`guidance`、`reason`、`evidence_refs`、`status`（`uncertain` / `confirmed` / `outdated` / `vetoed`）、`version`、`supersedes_id`、`superseded_by`、`created_at`、`updated_at`；
   - 所有模型产出候选初始状态均为 `uncertain`（永不自晋升），**不注入当前对话 `HEAD`**；
   - 暴露统一查询与治理接口：`experience_interface_contract()`、`list_experience_candidates()`、`query_experience_candidates(situation=..., kinds=...)`、`decide_experience_candidate(candidate_id, action)`（支持 Lan 确认/否决，否决后同类不再复活）。
5. **后台运行、崩溃恢复与安全**：
   - 独立 `PeriodicBackgroundTick`（每 300s 检查一次是否到达 `interval_hours` 且未超 `max_runs_per_day` / 退避窗口），不占用聊天主锁；
   - `in_flight` 持久化 + 重启自动调用 `recover_interrupted()` 与 `budget.reconcile_unsettled()`，把中断请求标记为 `interrupted` 并按保守上限结清未结算预留；
   - 脱敏运行日志 `data/<role>/cognitive-consolidation-runs.jsonl` 仅记录统计、证据引用与截断摘要，过滤密钥/凭据文本；
   - 结算费用精确按 `Budget.settle()` 同口径记账，并在 `/cost`（`Budget.display()`）与 `/api/budget/summary` 单独列示「认知巩固」，不混入聊天费用。

## 4. 系统接入矩阵（`integration-complete` 八项）

| 接入维度 | 现状与实际证据 |
| --- | --- |
| **1. 入口** | `Chat.__init__` 实例化 `self.consolidation`；API `GET /api/brain/consolidation`、`POST /api/brain/consolidation/settings`、`POST /api/brain/consolidation/run`、`POST /api/brain/consolidation/experience`；Brain 首页独立开关/参数卡片、Brain「关系与成长」页巩固结果与 P7 候选确认/否决区、费用管理页独立预算与频率卡片；Electron `desktop/main.cjs` 显式放行全部路由（主窗与独立小窗）。 |
| **2. 停止与恢复** | `start_short_term_notes_scheduler` / `stop_short_term_notes_scheduler` 同生命周期启停；重启时 `recover_interrupted()` 清理在途状态并结清未结算预算预留；连续失败指数退避（15m → 30m → 60m → 120m）。 |
| **3. 权限与主权** | 全部写接口继承现有本地 token + Origin/Host 守卫；锁定/用户确认/否决条目受 S3 与 S6-1 硬拦截，不可被后台巩固覆盖或复活；`memory.consolidation.prompt` 注册为不可随意篡改的系统提示词。 |
| **4. 费用** | 独立账本类目 `cognitive_consolidation`，默认上限 `¥0.05/日`、默认最多 `2 次/日`、默认间隔 `24h`、默认关闭；共享总预算与类目预算双门控；`/cost` 与费用管理页独立展示当日/当月已用与上限。 |
| **5. 数据** | 角色隔离文件：`data/<role>/cognitive-consolidation.json`（配置）、`cognitive-consolidation-state.json`（游标与 P7 候选）、`cognitive-consolidation-runs.jsonl`（脱敏运行记录）；认知写回 `learned-context.json`，重要度写回 `brain.db`（经 `MemoryWeights.propose()` 保持 canonical digest 一致）。 |
| **6. 状态可见** | Brain 首页实时展示开关状态、实际生效状态、上次运行结果/时间/费用、今日次数与花费、待审 P7 候选数；Brain「关系与成长」页展示每次巩固的新增/修订认知、证据来源、形成原因、S3 实际生效状态、S6-1 重评结果与 P7 候选确认/否决操作；`config_registry.py` 收录全部只读配置项。 |
| **7. 升级与回滚** | 默认关闭（`enabled=False`），对未开启用户零额外调用与零行为改变；关闭开关即刻停止后台调度，既有 S3/S6-1 数据与历史版本完整可查可撤回。 |
| **8. 真实环境验证** | 在 VEIL Lab 隔离副本（基于完整生产快照 `peter-20261127T170245/r3`，mock 模型 ¥0，零触碰正式 `data/peter`）完成跨日全链路演练与 `veil-lab run verify` 通过（run `p6-s6-3-final-20261128T074609`），详见验收报告 `docs/reports/p6-s6-3-cognitive-consolidation-20261128.md`。 |

## 5. 影响与回归矩阵（`impact-complete`）

| 改动文件 | 影响模块 | 回归验证 |
| --- | --- | --- |
| `veil/prompts/defaults/memory.consolidation.prompt.md`、`veil/prompts/registry.py` | 提示词注册表、内联提示词扫描器 | `tests/test_prompt_registry.py` 通过 |
| `veil/brain/cognitive_consolidation.py` | S3 `LearnedContext`、S6-1 `MemoryWeights`、S6-2 `autonomy.jsonl`、共享 `Budget` | `tests/test_p6_s6_3_cognitive_consolidation.py` + VEIL Lab `p6-s6-3-final-20261128T074609` |
| `veil/core/veil_agent.py`、`veil/core/config_registry.py` | `Chat` 启动/停止、`/cost` 展示、全量配置清单 | `tests/test_tool_budget.py`、`tests/test_config_registry.py`、`tests/test_autonomy.py` |
| `veil/brain/brain_p4_api.py`、`veil/server/web_agent.py` | Brain API、`GET /api/budget/summary` | `tests/test_p4_p5_brain_routes.py`、`tests/test_budget_workspace_route.py`、`tests/test_p6_s6_3_cognitive_consolidation.py` |
| `desktop/main.cjs`、`desktop/workspace-brain.js`、`desktop/workspace-budget.js` | Electron 路由白名单、Brain 首页/关系与成长页、费用管理页 | `node --test desktop/tests/*.test.cjs`（69/69 通过，含新增 `cognitive-consolidation.test.cjs` 4 项） |

## 6. 对照「做完后的效果」的诚实审视与未证明边界

按工单要求，在提交 PR 前从真实日常使用场景重新审视「Peter 是否真正具备从经历中学习、形成认知并持续成长的能力」：

### 已真实打通并有证据证明的闭环
1. **端到端学习与注入闭环已真实接通**：新聊天记忆 + 历史锚点 + 审计后的自主经历 → 巩固分析 → S3 `LearnedContext`（含 `RelationshipSemanticWorker` 语义核验与四区独立预算）→ 后续聊天与自主唤醒的 `stable` 与 `relationship_base` 提示词投影，已在单测与 VEIL Lab 完整生产快照上跑通。
2. **跨日演进与主权防线已验证**：第 1 天形成的新认知在第 2 天被新证据修订后，旧版退为 `outdated` 并保留 `superseded_by`；锁定/否决条目被强拦截；幻觉引用、重复总结、S6-2 不洁自主输出、纯自主自猜均被挡在晋升门外；S6-1 重要度重评可从巩固证据链触发且不重复提权。
3. **长期稳定运行基础设施已完备**：默认关闭、独立调度、精确费率记账、日预算与次日恢复、崩溃重启结清在途预留、配置持久化与 UI 完整可视化。

### 尚未证明 / 明确不属于本单已解决的边界（绝不夸大）
1. **真实日用 LLM 的多日自然归纳质量与主观成长体验尚未经实盘验证**：本单严格遵守红线未擅自追加付费 `--live` 实验，VEIL Lab 使用离线 mock 响应验证了完整数据、预算、治理与注入闭环，但真实模型在连续 7–14 天真实聊天噪音下产出的洞察是否“足够有悟性、不流于套话、不频繁制造低价值待审项”，必须等合并部署后由 Lan 开启灰度体验实测（这也是默认关闭的核心原因）。
2. **P7 情境经验尚处于「沉淀与治理就绪、未注入对话」阶段**：本单交付了 `p7.experience.v1` 结构化存储、演进与确认/否决界面及查询契约，但按要求未注入 `HEAD`，也未接管对话或工具行为决策；真正让经验在匹配情境下影响行为，属于后续 P7 工单。
3. **S5 延期项仍未关闭**：`docs/tickets/460-p6-s5-retrieval-judge-association.md` 记录的“情境自然联想隐式工作 `trap` 穿透 + 同轮判官联合门控”不在本单范围，仍保持未关闭。
4. **Windows 真机 UI 实点未执行**：桌面前端已完成 69/69 jsdom 与白名单自动化测试，但真实 Electron 窗口点击与视觉排版需 Lan 在验收清单 B-13 体验确认。

## 7. Lan 验收步骤

详见 [`docs/ACCEPTANCE.md`](../ACCEPTANCE.md) **§3 B-13**。
