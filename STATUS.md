# 当前状态

## P3-0 配置持久化与前置准备（本 PR，待复审）

- **目标**：解决一键更新会把 Brain 接管配置冲回 Ombre 影子模式的问题；固化 P3 方案/工单、备份恢复与误删核实；不改变正式 Peter 当前行为。
- **配置持久化**：把 P2 正式切换的过渡配置（`[memory.brain] write_authority=true`、`shadow=false`、`[memory.retrieval] backend=brain`）提交进 main；新增 `tests/test_brain_defaults.py` 防回退。未采用 `character.local.toml` 永久覆盖层（理由见 `docs/tickets/p3/P3-0.md`）。
- **P3 资料**：`docs/tickets/p3/` 含方案与 P3-0..P3-4 五张工单；原 P3-2（停 Ombre+观察）并入 P3-1，不再单独出代码单。
- **预检备份/恢复**：已在仓库外完成正式 brain.db 在线备份、导出、manifest、离线 restore drill（integrity/digest/计数通过）；备份与真实数据不提交仓库。
- **误删记忆**：核实切换后验收窗口误删 1 条真实旧记忆（非 canary），已按 Lan 批准从 P2 备份定点恢复原 ID/正文/版本/向量/事件关联；恢复报告留仓库外。后续测试只用新增专用测试记忆。
- **未做**：未启动 P3-1；未停止/重启正式 Peter；未改正式数据库以外数据；未自行合并 PR。

## P2 正式接管（2026-11-25，已完成）

- 正式配置：Brain 唯一写入权威、shadow 关闭、自动召回 backend=brain；Ombre 仍在 18011，仅临时回退，未卸载。
- 数据：P2 切换与标签治理完成；正文/ID/版本/事件/来源/关联不变；0 pending、digest ok。
- 回退：当前 Brain 已有同 ID 导出；独立 Ombre 读取验证通过。详见 `docs/tickets/p2/P2-formal-cutover-report.md`。

## 待 Lan 验收

1. **P2 正式体验**（P3-1 的硬 gate）：记忆页/事件页/搜索/锁定/归档/恢复/删除；连续聊天旧事召回、新事保存、事件续写、无重复注入、无 pending 静默；自主活动开关与费用；确认无双写。
2. P3-0 PR：复审配置持久化方案与 P3 工单；合并后按维护窗口说明确认更新后仍为 Brain 权威。

## 下一步

1. 等待 P3-0 PR 复审；不自行合并。
2. P2 正式体验通过后，再启动 P3-1（启动解耦 + Brain-only + 停用 Ombre）。
3. P3-2/P3-3/P3-4 按工单顺序；P3-4 不可逆清理需 Lan 维护窗口授权。
