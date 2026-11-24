# P2-B 自审（2026-11-24，复查完成版）

对照 `docs/tickets/p2/P2-B.md` 逐条核查：

## 已完成的隔离演练与质量证据

1. **读取接管矩阵**：
   - 开场记忆（`breath`）、搜索（`breath_search`）、上下文候选（`context`）全部切换至从 Brain 读取。
   - 判官（`recall_judge.py`）与 assisted 向量接入 Brain。
   - 页面/版本/事件读取经 `BrainRead`/`BrainStore` 接入。

2. **30 条人工真值问题集与质量评测**：
   - 建立了包含 24 条真实预期问题（基于 69 篇事件故事与聊天记忆）与 6 条负向无答案问题的真值测试集：[`docs/tickets/p2/evidence/golden_truth_30.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/golden_truth_30.json)。
   - 不再依赖旧 Ombre 注入候选作为正确答案。
   - 运行评测脚本 `scripts/p2_golden_eval.py`，评测报告已保存至 [`docs/tickets/p2/evidence/golden_truth_eval_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/golden_truth_eval_report.json)。
   - Top-5 命中率 54.2%，Top-8 命中率 62.5%，平均延迟 833.3 ms。

3. **事件故事与旧碎片不重复注入**：
   - 在真实模型隔离端到端测试 `scripts/p2_isolated_peter_real_e2e.py` 中验证，当提问仙人掌经历召回 V01 事件故事时，其关联旧碎片 `99df0c513389` 被 `_event_ledger()` 成功折叠，**0 重复注入**。
   - 执行日志：[`docs/tickets/p2/evidence/isolated_peter_real_e2e.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/isolated_peter_real_e2e.log)。

4. **真实 Ombre 进程切换协议与回退演练**：
   - 脚本 `scripts/p2_cutover_drill_live.py` 在独立端口真实启动 Ombre 常驻服务进程，完整演练了：
     - **真实启动与全量对账**：启动常驻 Ombre，运行 `ShadowSync.reconcile()` 对比 287 个源文件与数据库记录，0 差异；`check_db()` 验证 integrity=ok 与 digest_ok=True。
     - **一致性备份**：生成完整快照文件。
     - **权威锁定与旧写冻结**：切换 `write_authority=true, shadow=false`，双权威互斥校验通过；通过 `BrainAuthority` 写入金丝雀记忆，核验 Ombre vault 目录 287 个文件完全未变（旧写已冻结）。
     - **差异中止保护（Discrepancy Abort）**：向 vault 注入篡改内容，对账比对检出 `mismatches=1`，成功触发中止保护。
     - **真实失败回退演练（Failure Rollback）**：将 Brain 记忆全量导出回 vault，切回 Ombre 权威，哈希对账 0 差异，重启 Ombre 常驻服务；调用 Ombre 原生 MCP `breath_search` 成功检索到金丝雀记忆，**数据零丢失，回退通道完全安全**。
   - 执行日志：[`docs/tickets/p2/evidence/cutover_drill.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill.log)。
   - 统计报告：[`docs/tickets/p2/evidence/cutover_drill_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill_report.json)。

## 待 Lan 授权的正式切换事项

- 正式切换协议执行（备份、冻结正式 Ombre 写入、最终对账、开启 `write_authority=True`、重启 Peter）。
- 切换后由 Lan 在真实 Peter 上体验保存、修改、回忆并确认 P2 最终闭环。
