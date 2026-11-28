# HANDOFF

> 更新：2026-11-28 14:50 +08:00 · 最新 `main = a7615ab`（PR #469 已合并；正式桌面仍为 `d7fe9ec`）· 当前分支 `ticket/467-p7-0-experience-injection-plan` / **工单 467 · PR #468**（P7-0 施工规划已吸收审查意见并合入最新 main，待最终 CI 后合并）

## 当前停点

- **PR #469 已合并**（`a7615ab`）：B-01（召回与注入页合并索引 + `sent=False` 过滤）、B-02/B-03（事件阶段正文 `text` / 来源证据 / 状态中文语义）、B-05/B-07（关系底座可注入统计对齐 `build_prompt` + 确认/否决/恢复/查看证据治理闭环）、B-06（认知版本历史解析与行级 diff 展开）、C-01（7 日增长统计同口径）、C-02/C-03（健康期间与四区卡片布局）、A-01（H-7 历史哈希口径区分）。
- **三项授权正式数据维护已完成**（备份目录 `data/peter/backups/pre-469-maint-20261128-140438/`）：
  - A-01：238 条历史向量已用 `BAAI/bge-m3` 重建并写入 `meta.embedding_model`，H-7 复验 `severity=good`（226 活跃记忆 / 242 向量 1024 维全一致）。
  - A-02：57 处悬空引用（46 `relations` + 11 `sidecar_state.links_json`）先隔离验证再正式清理，H-3 复验 `dangling=0`（2 条无关联记忆保留）。
  - A-05：16 条历史失败日志核查均为 `out_len=0` 无正文草稿，无需写入，零重复/覆盖风险。
- **剩余工程验收已完成**：正式部署版本 `d7fe9ec`、真实检索/判官（最新轮 `judge.status=chosen`、检索降级 0 次）、缓存与费用记录、`health-settings.json` 持久化、`runtime-status.json` 均已核验正常。
- **P7 阻塞核查**：不存在阻塞 P7 开工的实际故障；非阻塞遗留（P3-REM-01/02、P6-S5-01、P6-RERANK-01、P6-LONG-01）统一纳入 P8。
- **PR #468 规划已修订**：保留 P7-S1~S5；补齐否决/恢复、协同备份、原子反馈、权限隔离、待验证方案第一次低风险实践机会；取消 19–28 天人工观察，改为自动测试、Brain 主动体检、数据检查和 10 场景回放。

## 状态入口

当前进度、PR、部署与阶段结论只看 [STATUS.md](STATUS.md)；Lan 验收状态只看 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)；证据矩阵、遗留编号与 P7 接入条件见 [专项审计报告](docs/reports/p2-p6-delivery-summary-p7-readiness-20261128.md)。本文件不重复维护状态表。

## 遗留边界

- P3-REM-01：`web_agent.py` 仍有 Ombre 用户文案/兼容诊断。
- P3-REM-02：历史 `_agent/state.jsonl` 的 `hidden` 仍可影响读取。
- P6-S5-01：隐式工作 `trap` + 同轮判官联合门控未关闭。
- P6-RERANK-01：Qwen3 Reranker 仅实验/契约，未上线。
- P6-LONG-01：动态评价、保护性衰减、自主联想、认知巩固的多日真实效果待 P8。
- P7-WIRE/EVIDENCE/MATCH/SIGNAL/BEHAVIOR：provider、全类型证据新鲜度、语义匹配、触发信号、冲突/回退/行为诊断待 P7 实现。
- 正式恢复和 Embedding 重建仍必须先 dry-run，经 Lan 明确授权后执行。

## 下一个窗口第一步

1. 推送 PR #468 的规划修订与最新 main 合流提交，等待最终 CI 后合并。
2. #468 合并后从最新 main 开独立工单/worktree 启动 P7-S1：接通角色隔离的 `experience_hint_provider`（不得直接读候选 JSON 绕过主权过滤）。
3. 非阻塞遗留（P3-REM-01/02、P6-S5-01、P6-RERANK-01、P6-LONG-01）不延迟 P7 开工。

## 红线

- 不启停正式 Peter、不向正式 Peter 发测试消息、不写正式 `data/peter`。
- 不追加付费实验；不提交真实聊天、记忆、人设、密钥、本机绝对路径或日志。
- 本单只修改 P7 规划文档并合入 #469 的既有代码；不启停或更新正式 Peter，不写正式数据。
