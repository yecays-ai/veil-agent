# HANDOFF

> 更新：2026-11-28 12:22 +08:00 · 基线 `main = a5db802`（工单 465 / PR #466 · P6-S7 已合并）· 当前分支 `ticket/466-p2-p6-delivery-audit` / worktree `worktrees/466-p2-p6-delivery-audit` / 工单 466 / PR #467（专项只读审计；待 CI 与 Lan 审阅，未部署）

## 当前停点

- P2–P6 原始需求、PR #434–#466、CI、部署、Lan 验收与 P7 前置条件已完成只读核查。
- 总报告：`docs/reports/p2-p6-delivery-summary-p7-readiness-20261128.md`。
- 结论：P2–P6 主工程链已交付到 main，但部署、Lan 验收和长期效果未闭环，P3 另有两处静态残留，因此不能称完整交付。
- P7 可有条件开工：证据链、状态机、经验候选治理和 4 条/600-token AUXILIARY 槽位可复用；生产 `experience_hint_provider` 未安装，是首要施工项。
- 本单只改 Markdown；未启动/停止正式 Peter，未访问或写正式数据，未部署，未运行付费实验，未启动 P7。

## 关键事实

| 事项 | 当前事实 | 证据入口 |
| --- | --- | --- |
| P6-S7（工单 465 / PR #466） | 已合并 `a5db802`；Actions 测试/安全检查成功；未部署、无 Lan 验收 | `docs/ACCEPTANCE.md` B-14 |
| PR #434–#466 | 33/33 已合并 | 审计报告 §5 |
| PR #464 | Actions 成功；最新 Sourcery review FAILURE | 审计报告 GOV-01 |
| P2/P3 | 已部署并有开发/维护验证；无整体 Lan 验收 | 报告 §2.1–2.2 |
| P4/P5 | #456 已部署；#459 部署未知；#462 未部署；均无 Lan 验收 | 报告 §2.3–2.4 |
| P6 | S0–S7 已合并；S3–S7 无部署证据；终验归 P8 | 报告 §2.5–2.6 |
| P7 | 候选形成/治理/查询与槽位就绪；生产 provider 未接线 | 报告 §7 |

## 遗留边界

- P3-REM-01：`web_agent.py` 仍有 Ombre 用户文案/兼容诊断。
- P3-REM-02：历史 `_agent/state.jsonl` 的 `hidden` 仍可影响读取。
- P6-S5-01：隐式工作 `trap` + 同轮判官联合门控未关闭。
- P6-RERANK-01：Qwen3 Reranker 仅实验/契约，未上线。
- P6-LONG-01：动态评价、保护性衰减、自主联想、认知巩固的多日真实效果待 P8。
- P7-WIRE/MATCH/SIGNAL/BEHAVIOR：provider、语义匹配、触发信号、冲突/回退/行为诊断待 P7 实现。
- 正式恢复和 Embedding 重建仍必须先 dry-run，经 Lan 明确授权后执行。

## 下一个窗口第一步

1. 查看工单 466 / PR #467 的 CI；只处理文档事实、链接、编号和状态一致性问题。
2. PR 打开并有 CI 结果后暂停；不自行合并、部署或启动 P7。
3. 合并/部署缺失阶段后，Lan 按 `docs/ACCEPTANCE.md` 的“P2–P6 基础体验验收”一次性体验。
4. P7 如获授权，从 `main a5db802` 另开工单，首项接通角色隔离 provider；不得直接读候选 JSON 绕过主权过滤。

## 红线

- 不启停正式 Peter、不向正式 Peter 发测试消息、不写正式 `data/peter`。
- 不追加付费实验；不提交真实聊天、记忆、人设、密钥、本机绝对路径或日志。
- 本单不改运行代码、配置或正式数据，不部署，不合并，不启动 P7。
