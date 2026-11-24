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
   - 在隔离 Peter 端到端脚本 `scripts/p2_isolated_peter_e2e.py` 中验证，当检索到 V01 事件故事时，其关联旧碎片 `99df0c513389` 被 `_event_ledger()` 成功折叠，**0 重复注入**。
   - 执行日志：[`docs/tickets/p2/evidence/isolated_peter_e2e.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/isolated_peter_e2e.log)。

4. **切换协议端到端演练**：
   - 脚本 `scripts/p2_cutover_drill.py` 完整演练了：
     - **Happy Path**：一致性备份 → 冻结旧写入 → 最终对账（0 diff）→ 锁定单一写入权威 → 切换并验证读写。
     - **Failure Injection Path**：故意注入未对账差异，成功触发 ABORT 停机保护，保持单一权威不切，未发生双写。
   - 执行日志：[`docs/tickets/p2/evidence/cutover_drill.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill.log)。

5. **回退演练**：
   - `scripts/p2b_rollback_check.py` 实际启动独立 Ombre 进程，验证新增/修改可见、已删除数据不复活。

## 待 Lan 授权的正式切换事项

- 正式切换协议执行（备份、冻结正式 Ombre 写入、最终对账、开启 `write_authority=True`、重启 Peter）。
- 切换后由 Lan 在真实 Peter 上体验保存、修改、回忆并确认 P2 最终闭环。
