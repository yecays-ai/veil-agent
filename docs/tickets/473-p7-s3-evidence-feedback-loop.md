# 工单 473 · P7-S3 四级证据晋升、自主判断与反馈演化闭环

- **产品格子**：A 陪伴核心（衔接 B 电脑干活工具实践与自主生活）。
- **做完后 Lan 能多做的一件事**：Peter 既不会把自我猜测或未经验证的想法固化成“经验”，又能对已注入经验结合当下情境做出反驳、悬置或提出修订，并把真实工具结果、承诺履约和用户反馈沉淀为后续演化的客观依据。
- **Lan 怎么验收**：在隔离假数据下即时核验四级证据分域晋升、低风险单次实践队列、模型工具循环调用 `experience_reflect`、并发幂等与崩溃恢复、以及失败降级后经巩固修订并在下轮实际生效；P7 统一体验验收仍留在 P7-S5。

## 目标

严格按 `docs/reports/p7-0-experience-injection-construction-plan-20261128.md` §7.2 落地 P7-S3：实现 L1–L4 四级证据程序化核验与分域授权晋升（禁循环自证及未经实证的经验注入）、低风险单次实践队列、`memory_admin` 组 `experience_reflect` 自主判断工具及按需扩组可发现性、原子幂等可恢复的 `experience-feedback.jsonl` 账本与 `pending_delivery` 机制，并打通经验注入 → 实际行为 → 客观反馈 → 认知巩固 → 经验修订的完整闭环；不提前施工 S4/S5，不修改正式数据，不部署。

## 当前状态

- 基线：`main = 64fa112`（工单 471 · PR #472 `feat(P7-S2): hybrid experience matching and monotonic vector cache generations` 已于 `2026-11-28 15:40:20 UTC` 合并，GitHub Actions Tests / audit / scanner-report-smoke / Sourcery 均通过；未部署；Lan 即时体验统一留到 P7-S5）。
- GitHub Issue：`#473`；分支 / worktree：`ticket/473-p7-s3-evidence-feedback-loop` / `worktrees/473-p7-s3`。
- 直接复用：P7-S1/S2 `ExperienceHintProvider`、`EvidenceValidator`、`SituationEnvelope`、角色写锁 `brain_lock.write_lock`、S3 `LearnedContext` 与 `RelationshipSemanticWorker.verify_claim()`、`CognitiveConsolidator` 增量巩固与 `pending_commit` 零重付费续传、`tool_groups.py` 按需扩组与预算检查、`brain_backup.py` 协同备份。
- 复用规划调研：沿用 P7-0 §3.5 对 Nocturne `Mirror / Door` 的结论（不引第三方依赖、不造平行系统；Mirror 审视仅更新可观测验证账本与 `uncertain` 修订草案，绝不凌驾于 Lan 主权或篡改已确认正文）。
- 独占文件：`veil/brain/experience_feedback.py`（新增）、`veil/brain/experience_provider.py`、`veil/brain/cognitive_consolidation.py`、`veil/core/veil_agent.py`、`veil/autonomy/autonomy.py`、`veil/brain/storage/brain_backup.py`、`veil/tools/tool_groups.py`、`veil/prompts/inline.py`、`veil/prompts/defaults/memory.experience.hints.md`、`veil/prompts/defaults/memory.consolidation.prompt.md`、`tests/test_p7_s3_evidence_feedback_loop.py`、`tests/test_tool_budget.py` 及本单状态/验收文档。

## 验收步骤

1. 在隔离环境中分别投喂 L3/L4 自评、L1 用户原话、L2 客观工具（单次 vs 两次独立运行）和 L2 承诺记录，核验循环自证率严格为 0%，且 `relationship_consensus`、`commitment_accountability`、`work_method`/`autonomous_practice` 三个授权域互不越权。
2. 创建高风险与低风险 `unverified_improvement` 候选，核验即使手工 `confirm` 也返回 409 且正常槽位 0 注入；低风险方案仅获一次带 `trial_id` 的实践机会，获得 1 次 L1 正向或 2 次独立 L2 正向后由巩固生成新的 `success` 候选并淘汰旧项。
3. 在开启 P7 的真实模型工具循环中验证 `experience_reflect` 位于 `memory_admin` 组（不在 `ALWAYS_ON`），通过 `expand_capability` 发现并调用 `suspend` / `propose_revision` 不篡改原已确认正文且立即生效冷却；核验并发重试、同请求多结果、`pending_delivery` 崩溃恢复，以及两次连续失败自动降级 → 巩固修订 → 下轮注入新版的完整闭环。
