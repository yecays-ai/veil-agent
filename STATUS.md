# 当前状态

## P2（A/B/C）隔离实现已合并，复查修正进行中，正式验收三项关键条件未满足

- **P2-A**（#434，`0f6028d`）：Brain 独立写入、事件 69/69、同 ID 导出、备份恢复；默认关闭。
  复查修正：`hard_delete` 补清 `sidecar_events` 与 `events.memory_id`（fix/p2-review-corrections）。
- **P2-B**（#436，`e4306ad`）：读取接管、隔离 Ombre 回退已实际跑通；A/B 50 条 Brain 24/50 vs Ombre 14/50，
  延迟 173 vs 482 ms。正式切换未执行。
  **⚠️ 未满足验收条件**：①人工真值黄金集（30 条，非旧 Ombre 注入）未建立；
  ②事件/碎片不重复注入未在真实聊天中验收；③切换协议端到端演练未执行。
- **P2-C**（#435，`affea8c`）：可逆标签清理工具；隔离副本清理后 tags=0，七类覆盖仅 36.8%，
  结论不保留固定七类；正式库未清理（待 Lan 授权）。
  复查修正：Brain 写入入口封堵自由 tags（fix/p2-review-corrections）。
- 正式 Peter 仍是 Ombre 写 + Brain 召回；`write_authority` 关闭。

## ⚠️ P2 未完成，不得报告 P2 完成，不得进入 P3

P2-B 三项关键条件需在 Lan 验收前补完：
1. 30 条人工真值问题集（含无答案情形，验证相关/漏/错误召回）
2. 事件/碎片不重复注入的真实聊天验收
3. 切换协议（冻结/对账/锁/切换/失败安全恢复）隔离端到端演练

## 下一步

1. 合并 fix/p2-review-corrections（本 PR）。
2. 补完 P2-B 三项关键条件。
3. Lan 按 `docs/tickets/p2/acceptance.md` 验收；授权后执行切换窗口。
4. P2-C 标签正式清理（需 Lan 授权）；145 条核实清单（需 P2-B 完成后）。
5. 通过后才进入 P3 下线 Ombre。
