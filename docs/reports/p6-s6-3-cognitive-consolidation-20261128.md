# P6-S6-3 后台认知巩固与经验提炼 · 验收与隔离验证报告

> 日期：2026-11-28 +08:00 · 工单 464 · 分支 `ticket/464-p6-s6-3-cognitive-consolidation` · 基线 `origin/main = 90846bd`
> 严守红线：零触碰正式 `data/peter`、未启停正式 Peter、零新增付费 API 调用（¥0）、未合并、未部署。

## 1. 验证总览

| 验证维度 | 执行方式 | 结果 |
| --- | --- | --- |
| 新增 Python 单测与端到端模拟 | `tests/test_p6_s6_3_cognitive_consolidation.py`（7 大端到端场景） | **全部通过** |
| 定向关联模块回归（Python） | 15 个测试模块（S6-3 / S6-1 / S6-2 / S5 / S3 / S6 门禁 / Prompt / Budget / Config / Brain API / Autonomy / Repo Layout） | **262/262 通过** |
| 桌面端与 Electron 白名单测试 | `node --test desktop/tests/*.test.cjs`（含新增 `cognitive-consolidation.test.cjs` 4 项） | **69/69 通过** |
| VEIL Lab 完整生产上下文隔离验证 | 快照 `peter-20261127T170245/r3` · run `p6-s6-3-final-20261128T074609` · mock 模型（¥0） | **通过（`veil-lab run verify` 核验结论：通过）** |
| 正式 Peter 数据完整性校验 | 实验前后对比 `data/peter` 文件数、总字节数与最新修改时间 | **100% 未变更（`formal_dir_unchanged: true`）** |

## 2. VEIL Lab 隔离运行结果（完整生产快照 `peter-20261127T170245/r3`）

- **实验目录**：`veil-lab/runs/p6-s6-3-final-20261128T074609`（报告：`runs/p6-s6-3-final-20261128T074609/reports/latest.md`，仅留本地隔离环境）
- **实测要点**：
  1. **默认关闭门控**：加载真实生产快照后，`enabled=False`，定时触发返回 `disabled`，零模型调用、零写入。
  2. **真实大库增量选批与 UTF-8 载荷适配**：在成熟生产库（大量历史高重要度记忆 + 真实 `autonomy.jsonl`）上，`select_batch()` 成功选出 `10` 条记忆与 `6` 条审计后自主记录，经近因+价值均衡与 `_fit_messages()` UTF-8 字节自适应裁剪，避免历史高重要度记忆饿死当天新记忆及中文 3 字节载荷超限。
  3. **S6-2 自主输出过滤与反循环自证**：向隔离 run 注入 1 条洁净自主活动（`output_audit.clean=True`）与 1 条第三人称旁白不洁记录（`output_audit.clean=False`），巩固输入严格剔除不洁记录；仅引用自主活动的关系猜想被 S3 压回 `uncertain`（`source='peter'`，`self_guess_promoted=0`）；引用不存在记忆 ID 的条目被拦截（`rejected_ungrounded=1`）。
  4. **Day 1 综合沉淀 + S6-1 联动 + 精确费用记账**：
     - 跨两段合成经历 + 真实用户原话形成的新关系认知经 `RelationshipSemanticWorker` 核验后晋升 `confirmed`；新 `growth` 认知落地生效；
     - S6-1 `MemoryWeights.propose()` 成功执行 `5 → 6` 版本化重评（`outcome='applied'`）；
     - 实际结算花费 `cost_cny = ¥0.00496`（与 `Budget.category_totals('cognitive_consolidation')` 一致，`/cost` 独立显示「认知巩固」）。
  5. **预算上限与崩溃恢复演练**：
     - 将日预算下调至 `¥0.0001` 后手动触发返回 `budget_exhausted`，未发送模型请求；
     - 构造进程崩溃残留 `in_flight` 记录并重启新实例，启动阶段自动完成中断恢复（`last_status='interrupted'`）并结清未结算预留。
  6. **Day 2（+25h）冲突修订 + 主权拦截 + P7 候选演进 + 频控幂等**：
     - 新经历带用户原话对 Day 1 关系认知提出修订，经 S3 核验晋升为新版 `confirmed`，旧版保留并转为 `outdated`（`superseded_by` 指向新版）；
     - 试图覆写已锁定关系认知的修订被拦截（`effective_status='blocked_sovereignty'`），锁定项纹丝不动；
     - P7 情境经验候选由 Day 1 版本演进至 Day 2 版本，旧候选标记 `outdated` 且记录 `superseded_by`；
     - 紧接着再次定时触发，被 `interval_not_elapsed` 跳过（¥0 重复调用）；
     - `brain.db` 的 `quick_check='ok'` 且 `digest_ok=True`；后续 `profile.build_prompt()` 与 `relationship_base.build()` 均包含演进后的新认知。
  7. **`veil-lab run verify` 全栈核验**：对改造后的隔离 run 执行官方 `veil-lab run verify p6-s6-3-final-20261128T074609`，参考请求 `6f97daa416674907908a40704ca6e6a6` 与实际请求 `8ab21cd2abb64e509793984a6fd565ab` 模块装配核验结论为 **通过**。

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

## 4. 隔离验证中捕获并当场修复的两个真实生产隐患

本次 VEIL Lab 完整生产快照演练并非走过场，实际发现了 3 个单元测试未暴露的真实生产问题并已全部修完并补测锁定：
1. **成熟大库历史记忆饿死新记忆与中文 UTF-8 载荷超限**：生产库有大量历史高重要度记忆且中文 3 字节展开，初始选批与固定截断导致当天新记忆排不进前 10 且载荷超出上限 → 已改为“近因+价值均衡选批”与 `_fit_messages()` UTF-8 字节自适应裁剪。
2. **保守预留超过默认 `¥0.05/日` 上限**：初始 `MAX_INPUT_TOKENS=24000` / `MAX_OUTPUT_TOKENS=2048` 在 ¥2/M+¥8/M 费率下保守预留 `¥0.0644 > ¥0.05`，会导致新的一天首轮即误报 `budget_exhausted` → 已下调至 `16000 / 1400` 并加入预算感知字节上限。
3. **`Budget.settle()` 返回布尔值导致运行日志费用误记为 `1.0`**：`Budget.settle()` 返回 `True`（而非金额），原 `float(settled)` 把运行日志费用误记为 `¥1.0`，且 `/cost` 未单列 `cognitive_consolidation` → 已改为按同口径计算精确结算金额，并在 `Budget.display()` 单列「认知巩固」且补充严格断言测试。

## 5. 残留边界说明

1. 真实日用模型下的多日巩固洞察质量与“是否真正有悟性”需在灰度开启后由 Lan 亲自体验判断；
2. P7 情境经验尚未参与对话/行为路由，仅完成沉淀、演进与人工确认/否决接口；
3. S5 延期项（隐式工作 `trap` 穿透 + 同轮判官联合门控）仍未关闭，留待专项处理；
4. Electron 真机视觉与点击验收留待 Lan 按 `docs/ACCEPTANCE.md` §3 B-13 完成。
