# HANDOFF

> 更新：2026-11-28 14:15 +08:00 · 基线 `main = d7fe9ec`（正式桌面已部署）· 当前分支 `ticket/468-p2-p6-acceptance-fixes` / PR #469（P2–P6 正式环境验收缺陷修复、三项授权数据维护与剩余工程验收收口；待 CI/Sourcery 通过后合并，合并后即可启动 P7）

## 当前停点

- **PR #469 已修复**：B-01（召回与注入页合并索引 + `sent=False` 过滤）、B-02/B-03（事件阶段正文 `text` / 来源证据 / 状态中文语义）、B-05/B-07（关系底座可注入统计对齐 `build_prompt` + 确认/否决/恢复/查看证据治理闭环）、B-06（认知版本历史解析与行级 diff 展开）、C-01（7 日增长统计同口径）、C-02/C-03（健康期间与四区卡片布局）、A-01（H-7 历史哈希口径区分）。
- **三项授权正式数据维护已完成**（备份目录 `data/peter/backups/pre-469-maint-20261128-140438/`）：
  - A-01：238 条历史向量已用 `BAAI/bge-m3` 重建并写入 `meta.embedding_model`，H-7 复验 `severity=good`（226 活跃记忆 / 242 向量 1024 维全一致）。
  - A-02：57 处悬空引用（46 `relations` + 11 `sidecar_state.links_json`）先隔离验证再正式清理，H-3 复验 `dangling=0`（2 条无关联记忆保留）。
  - A-05：16 条历史失败日志核查均为 `out_len=0` 无正文草稿，无需写入，零重复/覆盖风险。
- **剩余工程验收已完成**：正式部署版本 `d7fe9ec`、真实检索/判官（最新轮 `judge.status=chosen`、检索降级 0 次）、缓存与费用记录、`health-settings.json` 持久化、`runtime-status.json` 均已核验正常。
- **P7 阻塞核查**：不存在阻塞 P7 开工的实际故障；非阻塞遗留（P3-REM-01/02、P6-S5-01、P6-RERANK-01、P6-LONG-01）统一纳入 P8。

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

1. 工单 466 / PR #467 的 CI 与审查已通过；等待 Lan 审阅。
2. 保持暂停；不自行合并、部署或启动 P7。
3. 合并/部署缺失阶段后，Lan 按 `docs/ACCEPTANCE.md` 的“P2–P6 基础体验验收”一次性体验。
4. P7 如获授权，从 `main a5db802` 另开工单，首项接通角色隔离 provider；不得直接读候选 JSON 绕过主权过滤。

## 红线

- 不启停正式 Peter、不向正式 Peter 发测试消息、不写正式 `data/peter`。
- 不追加付费实验；不提交真实聊天、记忆、人设、密钥、本机绝对路径或日志。
- 本单不改运行代码、配置或正式数据，不部署，不合并，不启动 P7。
