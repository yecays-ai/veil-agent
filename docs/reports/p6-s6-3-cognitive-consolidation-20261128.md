# P6-S6-3 后台认知巩固与经验提炼 · 验收与隔离验证报告

> 日期：2026-11-28 +08:00 · 工单 464 · 分支 `ticket/464-p6-s6-3-cognitive-consolidation` · 基线 `origin/main = 90846bd`
> 严守红线：零触碰正式 `data/peter`、未启停正式 Peter、零新增付费 API 调用（¥0）、未合并、未部署。

## 1. 验证总览

| 验证维度 | 执行方式 | 结果 |
| --- | --- | --- |
| 新增 Python 单测与端到端模拟 | `tests/test_p6_s6_3_cognitive_consolidation.py`（14 大端到端与复查回归场景） | **全部通过** |
| 定向关联模块回归（Python） | 15 个测试模块（S6-3 / S6-1 / S6-2 / S5 / S3 / S6 门禁 / Prompt / Budget / Config / Brain API / Autonomy / Repo Layout） | **269/269 通过** |
| 桌面端与 Electron 白名单测试 | `node --test desktop/tests/*.test.cjs`（含新增 `cognitive-consolidation.test.cjs` 6 项） | **71/71 通过** |
| VEIL Lab 完整生产上下文隔离验证 | 快照 `peter-20261127T170245/r3` · run `p6-s6-3-r2-20261128T082548` · mock 模型（¥0） | **通过（`veil-lab run verify` 核验结论：通过）** |
| 正式 Peter 数据完整性校验 | 实验前后对比 `data/peter` 文件数、总字节数与最新修改时间 | **100% 未变更（`formal_dir_unchanged: true`）** |

## 2. VEIL Lab 隔离运行结果（完整生产快照 `peter-20261127T170245/r3`）

- **实验目录**：`veil-lab/runs/p6-s6-3-r2-20261128T082548`（报告：`runs/p6-s6-3-r2-20261128T082548/reports/latest.md`，仅留本地隔离环境）
- **实测要点**：
  1. **默认关闭门控（定时与手动一致）**：加载真实生产快照后，`enabled=False`，定时与手动 `force=True` 均返回 `disabled`，零模型调用、零写入。
  2. **真实大库增量选批、UTF-8 载荷适配与自主三态审计**：在成熟生产库上，`select_batch()` 选出 `10` 条记忆，并将历史未带 `output_audit` 的 `335` 条既有自主记录归类为 `unaudited` 严格排除（`audited_clean_autonomy=0, excluded_unaudited_autonomy=335`），避免把未经 S6-2 审计的旧输出当作事实沉淀；经近因+价值均衡与 `_fit_messages()` UTF-8 字节自适应裁剪，确保实际发送集合与证据白名单/处理指纹/诊断一致。
  3. **S6-2 自主输出三态过滤与反循环自证**：向隔离 run 注入洁净（`clean=True`）、不洁净（`clean=False`）与无审计（缺失 `output_audit`）三条自主活动，仅洁净记录进入载荷；纯自主猜想被 S3 压回 `uncertain`（`self_guess_promoted=0`）；幻觉引用被拦截（`rejected_ungrounded=1`）。
  4. **Day 1 综合沉淀 + S6-1 联动 + `CacheUsage` 序列化与脱敏日志**：
     - 跨两段合成经历 + 真实用户原话形成的新关系认知经 `RelationshipSemanticWorker` 核验后晋升 `confirmed`；新 `growth` 认知经内容级证据核验后落地生效；
     - S6-1 `MemoryWeights.propose()` 成功执行 `5 → 6` 版本化重评（`outcome='applied'`）；
     - 真实生产 `CacheUsage(tokens=400, status='reported', ...)` 正常序列化，结算花费 `cost_cny = ¥0.00456`（`/cost` 独立显示「认知巩固」）；`cognitive-consolidation-runs.jsonl` 经字段脱敏不含私密认知正文。
  5. **预算上限与崩溃恢复演练**：
     - 日预算下调至 `¥0.0001` 后触发返回 `budget_exhausted`，未发送模型请求；
     - 构造进程崩溃残留 `in_flight` 记录并重启新实例，启动阶段按当前角色范围自动完成中断恢复（`last_status='interrupted'`），结清未结算预留并将已派发中断请求计入当日次数上限。
  6. **Day 2（+25h）冲突修订 + 主权拦截 + P7 已确认保护 + 频控幂等**：
     - 新经历带用户原话对 Day 1 关系认知提出修订，经 S3 核验晋升新版 `confirmed`，旧版保留并转为 `outdated`（`superseded_by` 指向新版）；
     - 试图覆写已锁定关系认知的修订被拦截（`effective_status='blocked_sovereignty'`）；
     - Day 1 已由 Lan 确认的 P7 经验候选在 Day 2 收到新版本修订建议时保持 `confirmed` 有效（新版本 `outcome='pending_revision'`），直至 Lan 显式确认新版本后旧版才退为 `outdated`；
     - 紧接着再次触发，先触达 `daily_run_limit`（崩溃恢复 1 次 + Day 2 运行 1 次 = 2/2），调高上限后再触发被 `interval_not_elapsed` 跳过（¥0 重复调用）；
     - `brain.db` 的 `quick_check='ok'` 且 `digest_ok=True`；后续 `profile.build_prompt()` 与 `relationship_base.build()` 均包含演进后的新认知。
  7. **`veil-lab run verify` 全栈核验**：对隔离 run 执行官方 `veil-lab run verify p6-s6-3-r2-20261128T082548`，参考请求 `6f97daa416674907908a40704ca6e6a6` 与实际请求 `aa90347040de4ff49314be10536d2d4a` 模块装配核验结论为 **通过**。

## 3. 对照 PR #455 `AB-E1` 基线与原始需求覆盖审计

对照 `docs/reports/p6-s4-ablations-20261126.md`（§AB-E1）、`docs/reports/p6-s4-decision-table-20261126.md` 及 `docs/reports/p6-s4-requirements-coverage-audit-20261126.md`：

| 对照维度 | PR #455 `AB-E1` 基线发现 / 要求 | 本单 P6-S6-3 实现与实测对照 | 达成判断 |
| --- | --- | --- | --- |
| **默认状态与灰度策略** | 决策表要求默认关闭、独立预算上限、按灰度体验推进 | `enabled=False`、`interval_hours=24`、`max_runs_per_day=2`、`daily_budget_cny=0.05`，Brain 与预算页均提供独立开关和持久化 | **已达成** |
| **单次输入/输出与成本控制** | `AB-E1` 验证了低频摘要/巩固需严控单次 token 规模，防止后台静默刷费 | `MAX_INPUT_TOKENS=16000`、`MAX_OUTPUT_TOKENS=1400` + UTF-8 自适应裁剪 + 预算感知字节上限；典型单次约 `¥0.005`（远低于 `¥0.05/日`）；共享 `Budget` 双门控 + `in_flight` 恢复 | **已达成** |
| **防循环自证与证据溯源** | `AB-A2` / `AB-E1` 明确要求严禁自猜/无证据认知自动晋升（循环自证率 0%） | 100% 复用 S3 `LearnedContext` 与 `RelationshipSemanticWorker`；过滤 S6-2 不洁自主输出；幻觉引用拦截；纯自主无用户原话降级 `uncertain`；隔离测试自猜晋升 `0` | **已达成（结构防线）** |
| **跨经历综合与冲突演进** | 要求从多段经历提炼新认知，而非复述已有总结；冲突时保留旧版本 | 重复相似度跳过；`revision` 经 S3 核验后旧版退为 `outdated` 并写 `superseded_by`；锁定/否决强制拦截 | **已达成（闭环机制）** |
| **模块协作与 P7 预留** | 接通 S6-1 重评、S6-2 自主经历、Learned Context，并预留 P7 统一接口 | 接通 `MemoryWeights.propose()`（含去重/拒绝尊重）、读取 `autonomy.jsonl`、写入 `LearnedContext` 进入聊天与自主上下文；交付 `p7.experience.v1` 契约与确认/否决界面（不注入 `HEAD`） | **已达成** |
| **真实多日自然语言质量** | `AB-E1` 提醒离线合成样例不能替代真实多日主模型主观体验 | 本单按红线未跑付费 `--live` 实验；真实模型多日归纳质量待合并部署后由 Lan 灰度开启实测 | **待灰度实测（明确留白）** |

## 4. 独立复查与 GitHub 审查归并修复记录（PR #465 收尾）

在 PR #465 独立对照复查与本轮 GitHub（Sourcery / CodeRabbit）审查后，按根因归并修复了以下全部真实功能与安全缺陷，并均已补齐针对性单测与 VEIL Lab 回归：
1. **实际发送子集与白名单/指纹/诊断严格一致**：`_fit_messages()` 重建裁剪后的 `sent_batch`，仅将实际发给模型的记忆与自主记录纳入证据白名单与已处理指纹，未发送条目保留待处理资格；同时将 `title`/`meaning`/`why_remembered` 纳入记忆指纹，并排除 `delete_requests(status='pending')` 的待删除记忆。
2. **并发用户主权保护与 P7 生命周期治理**：模型请求结束后在互斥锁内重载最新状态合并，绝不覆盖请求期间 Lan 的确认/否决；P7 修订先校验相关性，且在 Lan 显式确认新版本前保留已确认旧版本有效；`confirm` 已否决候选返回 `409`；状态裁剪永不淘汰 `confirmed` 与 `vetoed` 候选（另保留永久 `vetoed_experiences` 账本）；UI 完整渲染所有返回候选并将待审项置顶。
3. **部分写入崩溃恢复与幂等**：`run_once()` 入口先清理异常残留的 `in_flight`；S3 采用内容确定性幂等键、S6-1 检查历史防重复提权、P7 采用内容键去重；`_apply_outputs()` 异常时安全落地 `apply_failed` 并记入 `runs_today`；S3 分区预算满等可重试拒绝不吞掉输入。
4. **自主经历三态审计与长历史不遗漏**：全量读取 `autonomy.jsonl` 并明确区分 `audited_clean`（`clean=True` 无问题）/ `audited_unclean`（`clean=False`）/ `unaudited`（缺失或损坏审计元数据），仅接纳已审计洁净记录并按时间顺序优先消费较早未处理记录。
5. **`facts`/`growth` 内容级证据核验与防提示注入**：提交 `facts`/`growth` 前剔除证据中的指令注入行并校验候选文本与引用来源的实际内容重合度（`facts` 另要求用户原话来源或至少 2 条记忆相互印证），拦截间接提示注入。
6. **生产兼容性、跨角色隔离与配置并发安全**：
   - 规范化生产模型 `CacheUsage` 数据类为可 JSON 序列化的 `cached_tokens` + `cache_status`；
   - `Chat.__init__` 使预算预留/结算配置与实际调用模型（`background_model` 或主 `model`）严格一致；
   - `Budget.reconcile_unsettled()` 增加 `character` / `request_ids` 过滤，禁止构造角色 B 巩固器误结清角色 A 的在途预留，并在 `Budget.settle()` 返回 `False` 时按 `settlement_conflict` 报错；
   - 关闭开关（`enabled=False`）同时拦截定时与手动触发，`force` 严格校验布尔类型且永不绕过开关或 `max_runs_per_day`，所有已派发失败请求均计入 `runs_today`，缺失顶层 JSON 字段拒绝解析；
   - 巩固配置引入乐观并发修订号 `settings_revision` / `expected_revision`（冲突返回 `409`），Brain 与预算页仅提交改动字段；`/api/config/overview` 接入 `memory.consolidation.*`；运行日志 `.jsonl` 做字段级脱敏，UI 详情保存在受保护角色状态中。
7. **opengrep SQL 告警核实**：原 `619`/`630` 行告警为静态规则误报（全部为硬编码常量列名，无外部输入），已进一步改为无拼接静态 SQL 字面量彻底消除告警。

## 5. 残留边界说明

1. 真实日用模型下的多日巩固洞察质量与“是否真正有悟性”需在灰度开启后由 Lan 亲自体验判断；
2. P7 情境经验尚未参与对话/行为路由，仅完成沉淀、演进与人工确认/否决接口；
3. S5 延期项（隐式工作 `trap` 穿透 + 同轮判官联合门控）仍未关闭，留待专项处理；
4. Electron 真机视觉与点击验收留待 Lan 按 `docs/ACCEPTANCE.md` §3 B-13 完成。
