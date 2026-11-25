# HANDOFF

## P3-4 维护窗口数据清理 + 物理卸载 + 最终验收（已完成，P3 结束）

- 正式清理已执行并验收：39 tombstones / 10 letters-plans / 24 文件目标；6 条旧 plan 完整归档；Ombre 运行目录/进程/18011 已移除。
- 正式 Peter 已重启，health ready；页面/事件/记忆 API 正常；无 18011/无 Ombre。
- 数据：240 memories / 234 versions / 240 vectors / 75 events / 76 stages / 593 event_ledger（含启动后正常 dream 新增）；正常记忆与冷备 P6 逐项 mismatch 0；quick_check/digest ok。
- 冷备、恢复演练、vault 导出、Brain 备份恢复均通过。最终验收报告：P3-4-maintenance/P3-4-final-acceptance-20261125.md。
- main 160b0ec 脚本与 #447 已验证版一致（sha256 97d5de...）；正式执行使用本地确定性修复版（sha256 d4e19e...），补丁 P3-4-maintenance/p3_4_cleanup-deterministic.patch 随下一次开发 PR 同步；未单独开 PR 等 CI。
- P3 结束；未启动 P4。

## P3-3 取消隐藏 + 去标签/domain 依赖 + 页面兼容（已合并 #444，已随 P3-4 部署）

- 隐藏机制/旧 tags/domain 页面依赖已移除；旧 hidden 行启动迁移；自主删除审批与 surprise 页面隐藏已修。
- 已随 P3-4 维护窗口部署。

## P3-2 旧系统清理 + 事件附账本统一（已合并 #443，已部署）

- 事件附账本/状态进入 brain.db；正式维护已完成迁移和验收；P3-4 已在其上完成清理。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；首次初始化/缺库拒建/损坏库不替换；CI 全绿。
- 已随 P3-2/P3-4 维护窗口部署；正式 Peter 当前运行 P3-4 代码。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口完成更新/重启/技术验收；P2 正式体验已由 Lan 确认通过。

## P2 正式接管（2026-11-25）

- Brain 唯一写入权威、shadow 关闭、retrieval=brain；Ombre 18011 仍运行仅作临时回退。
- 回退：按 `docs/tickets/p2/P2-formal-cutover-report.md` 第 6 节，不要用旧备份直接覆盖当前 Brain。
