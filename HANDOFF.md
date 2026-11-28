# HANDOFF

> 更新：2026-11-28 13:37 +08:00 · 基线 `main = d7fe9ec`（工单 466 / PR #467 · P2–P6 阶段交付总结与 P7 前置验收已合并，2026-11-28 05:24:46 UTC，Actions/Sourcery 全通过）· 当前分支 `ticket/467-p7-0-experience-injection-plan` / worktree `worktrees/467-p7-0-experience-injection-plan` / 工单 467（P7-0 专项只读设计；规划报告、工单、STATUS 与 HANDOFF 已完成，待提交 PR、跑 CI 后暂停等待 Lan 确认施工方案）

## 当前停点

- **工单 467 · P7-0 情境触发式经验注入与自主判断力完整施工规划**已完成只读设计与文档落盘。
- **完整规划报告**：[`docs/reports/p7-0-experience-injection-construction-plan-20261128.md`](docs/reports/p7-0-experience-injection-construction-plan-20261128.md)
- **工单文档**：[`docs/tickets/467-p7-0-experience-injection-plan.md`](docs/tickets/467-p7-0-experience-injection-plan.md)
- **核心设计要点**：
  - 零平行系统：复用 `brain.db` / `EventMemory`、`LearnedContext`（S3 语义核验与主权）、`CognitiveConsolidator`（`p7.experience.v1` 向后兼容扩展正交 `experience_type` / `domain` / `exceptions` / `origin_evidence_tier` / `verification`）、`Chat.p7_experience_hints_context()`（AUXILIARY 4 条 / 600 tokens / 仅 `confirmed` / `advice_only`）、同轮 `Memory._turn_query_vector` 与 S5 `context_signal`。
  - 证据与主权防线：建立 L1（用户原话反馈）/ L2（客观工具与任务结果）/ L3（洁净自主自述）/ L4（模型自评）四级证据体系；`relationship_consensus` 严守 L1+S3 或 Lan 确认；`work_method` / `autonomous_practice` 仅在二级开关 `auto_promote_objective=True`（默认关）且满足跨次 L2 客观实证 + S3 核验 + 无否决时允许程序晋升，并保留 Lan 一键否决与负反馈自动降级；`unverified_improvement` 验证前永不注入。
  - Nocturne `Mirror / Door` 适用边界：沿用 `p6-0-memory-audit-20261126.md` §3 核实结论（公开仓库无独立源码模块，只借设计思路），将 Door 映射为情境/例外阈值门控，将 Mirror 映射为自主判断姿态（采用/调整/反驳/悬置/提修订，经 `memory_admin` 组 `experience_reflect` 留痕，不越权覆盖 Lan 主权）。
  - 五阶段施工拆分：`P7-S1`（角色隔离生产 provider + 全类型证据新鲜度 + `SituationEnvelope` + `brain_backup.py` 协同备份补齐 `P7-BACKUP-01`）→ `P7-S2`（Stage 1 零新增 Embed 混合语义匹配 + 冲突裁决 + HEAD 去重 + 多入口契约）→ `P7-S3`（四级证据晋升 + 自主判断留痕 + 反馈回流巩固闭环）→ `P7-S4`（Brain 监控/关系成长/召回诊断 UI + H-8 探针）→ `P7-S5`（VEIL Lab 10 项体验场景隔离验收与 `ON/OFF` 对照），并衔接 P8 多日真机终验。
- 本单只改 Markdown；未修改运行代码、配置或正式数据，未启停正式 Peter，未新增付费实验，未启动 P7 施工。

## 状态入口

当前进度、PR、部署与阶段结论只看 [STATUS.md](STATUS.md)；Lan 验收状态只看 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)；P2–P6 交付审计见 [P2–P6 总结报告](docs/reports/p2-p6-delivery-summary-p7-readiness-20261128.md)；P7 施工规划见 [P7-0 完整施工规划](docs/reports/p7-0-experience-injection-construction-plan-20261128.md)。

## 遗留与前置边界

- `P7-WIRE-01` / `P7-EVIDENCE-01` / `P7-SIGNAL-01` / `P7-BACKUP-01`（本次新识别：`brain_backup.py` `COMPANION_FILES` 尚未收录巩固与 P7 状态文件）：归 `P7-S1` 首要关闭。
- `P7-MATCH-01` / `P7-BEHAVIOR-01`：归 `P7-S2` 与 `P7-S3`。
- `P6-S5-01`（隐式工作 `trap` + 同轮判官联合门控）：仍未关闭，建议独立小单与 `P7-S1` 并行关闭。
- `P4-DEP-01` / `P5-DEP-01` / `P2-ACC-01`：P4(#459)/P5(#462)/P6-S3~S7(#460–#466) 未完整部署且缺 Lan 基础体验 smoke；不阻塞 P7 隔离开发，但**硬阻塞正式环境开启 P7 灰度**。
- `P3-REM-01` / `P3-REM-02` / `P6-RERANK-01` / `P6-LONG-01` / `P6-S7-01` / `GOV-01`：维持既定归属（长期效果统一归 P8）。

## 下一个窗口第一步

1. 跟踪工单 467（P7-0）PR 的 CI 结果；**保持暂停，不自行合并、不部署、不启动 P7 施工**。
2. 等待 Lan 审阅 [`docs/reports/p7-0-experience-injection-construction-plan-20261128.md`](docs/reports/p7-0-experience-injection-construction-plan-20261128.md) 并对 §10 的 3 个拍板项（五阶段工单拆分、自主客观经验二级自动晋升开关、`P6-S5-01` 并行关闭）给出决定。
3. 方案获批并合并后，按批准范围从最新 `main` 开独立工单与 worktree 启动 `P7-S1`（同时按仓库规则清理已合并的 `worktrees/467-p7-0-experience-injection-plan`）。

## 红线

- 不启停正式 Peter、不向正式 Peter 发测试消息、不写正式 `data/peter`。
- 不追加付费实验；不提交真实聊天、记忆、人设、密钥、本机绝对路径或日志。
- 本单不改运行代码、配置或正式数据，不部署，不合并，不启动 P7。
