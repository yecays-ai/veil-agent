# HANDOFF

## P2 正式接管（2026-11-25）

- 已执行：正式 Peter 停止 → 最终同步 → 一致性备份 + 恢复验证 → 事件完整数据迁移 → P2-C 标签治理 →
  最终对账 → 启用 Brain 唯一读写权威 → 启动验证 → 重启验证 → Ombre 回退导出验证。
- 正式运行状态：`write_authority=true`、`shadow=false`、`retrieval=brain`；Ombre 18011 仍运行，仅回退。
- 数据：288 记忆 / 286 版本 / 249 向量 / 73 事件 / 74 阶段 / 171 sidecar；tags=0、pending=0；
  原有 287 个 ID 无缺失；切换期新增 `66320d2a1f76`。
- 备份：`VEIL-backups/P2-cutover-20261125T050617`（迁移前）、`P2-cutover-clean-20261125T050737`
  （事件迁移后）、`P2-cutover-final-20261125T050822`（启用前，已恢复验证）、
  `P2-authority-20261125T051444`（最终 brain/config/回退导出）。
- 未做：P3、Ombre 卸载、正式库以外的新功能。
- 下一步第一步：Lan 打开桌面体验报告第 7 节场景；确认前不要进入 P3。
- 回退：按 `docs/tickets/p2/P2-formal-cutover-report.md` 第 6 节走“当前 Brain 同 ID 导出 → Ombre 读取”，
  不要用旧备份直接覆盖当前 Brain。
