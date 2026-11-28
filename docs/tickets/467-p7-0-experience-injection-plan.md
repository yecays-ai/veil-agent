# 工单 467 · P7-0 情境触发式经验注入与自主判断力完整施工规划

属于 A 陪伴核心（衔接 B 电脑干活工具实践与自主生活）的只读专项设计；完成后 Lan 可基于一份完整施工蓝图确认 P7 的架构边界、证据晋升权限、分阶段施工顺序、费用与体验验收标准；验收方式是审阅规划报告并拍板是否按该方案启动 `P7-S1` 施工。

## 目标

以 PR #467 合并后的 `main = d7fe9ec` 为基线，复核 P2–P6 底座能力、PR #455 消融结论、Nocturne `Mirror/Door` 调研及 P6 遗留影响，制定 P7「情境触发式经验注入与自主判断力」的完整架构、数据流、状态流转、分阶段工单与 P7→P8 验收衔接方案。

## 当前状态与交付物

- **复用底座**：`brain.db` / `EventMemory`、`LearnedContext`（四区独立预算 + S3 语义核验 + 主权防线）、`CognitiveConsolidator`（`p7.experience.v1` + 候选治理 + `pending_commit`）、`Chat.p7_experience_hints_context()`（AUXILIARY 槽位，4 条 / 600 tokens / 仅 `confirmed` / `advice_only`）、同轮 `Memory._turn_query_vector` 与 S5 `context_signal`。
- **识别的硬缺口**：生产 `experience_hint_provider` 未安装（`P7-WIRE-01`）、仅核查 `memory:` 删除态未逐类核查证据新鲜度（`P7-EVIDENCE-01`）、情境信封未传向量与 S5 信号（`P7-SIGNAL-01`）、纯词法匹配漏召回改写与隐含场景（`P7-MATCH-01`）、缺自主判断留痕与反馈回流闭环（`P7-BEHAVIOR-01`），以及 `brain_backup.py` `COMPANION_FILES` 尚未收录巩固与 P7 状态文件（`P7-BACKUP-01`）。
- **交付报告**：[`docs/reports/p7-0-experience-injection-construction-plan-20261128.md`](../reports/p7-0-experience-injection-construction-plan-20261128.md)。
- **施工边界**：纯只读设计，未修改运行代码、配置及正式数据，未启停正式 Peter，未新增付费实验，未启动 P7 施工。

## 验收步骤

1. 审阅 [`docs/reports/p7-0-experience-injection-construction-plan-20261128.md`](../reports/p7-0-experience-injection-construction-plan-20261128.md) 中的 16 项原始需求覆盖矩阵、四级证据与分域晋升路径、Nocturne `Mirror/Door` 边界及 10 项真实用户体验验收场景。
2. 对报告 §10 的 3 个拍板项（五阶段工单拆分、自主/工具客观经验二级自动晋升开关、`P6-S5-01` 并行关闭节奏）给出决定。
3. 确认本 PR 后按需决定合并及后续 `P7-S1` 开工时机；本单不自行合并或启动施工。
