# P2-B 自审（施工中，2026-11-24）

## 已完成（隔离）

- 开场/搜索/context 从 Brain 读取；判官与 assisted 向量从 Brain 读取。
- 页面/版本/事件读取经 P2-A 的 BrainRead/BrainStore 已可读。
- 234 条 recall 测试 + Brain 组测试通过。
- 切换前检查脚本 `scripts/p2b_cutover.py`（backup/check/export/verify）与回退手册草案。

## 未完成

- 未在隔离实例中实际启动 Ombre 做回退；`verify` 只用回导 Brain 代替，**不满足工单**。
- 未建立黄金问题集做切换前后质量对比。
- 未跑事件/碎片不重复注入的真实聊天验收。
- 未执行切换协议（冻结/对账/锁/切换），只在文档里定义。
- 未覆盖 P2-C 清理后数据的回退（需在 C 合并后重跑）。

结论：**P2-B 未完成，不得合并、不得正式切换**。下一步先在隔离环境启动 Ombre 完成回退演练。
