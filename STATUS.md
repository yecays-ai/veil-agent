# 当前状态

## P3-2 旧系统清理 + 事件附账本统一（已合并 #443，已部署）

- **代码**：#443 合并于 `f63e58a`；正式目录已通过维护窗口更新到该提交。
- **正式部署**：2026-11-25 停服 → 完整冷备份（D:\Peter backup\VEIL-Agent-Backup-20261125T084139.218764Z-4688aa1f，verify 5427 文件）→ 更新 → 首次启动迁移 → 验收。事件附账本、events/stages、dream 状态已进入 brain.db；原文件归档到 `data/peter/archive/pre-p3-2-event-ledgers-20261125T164654`（只读，manifest 720 行）。
- **正式数据**：289 memories / 286 versions / 250 vectors / 75 events / 76 stages / 566 event_ledger / 1 state；quick_check ok、digest ok；与预迁移备份逐 ID 对比无缺失。页面/事件 API 正常，无 18011、无 Ombre。
- **回退**：完整冷备份 + 预迁移 Brain 备份；brain_backup 恢复会保留 safety copy/export 并合并备份后新增记录；P3-4 已在后续维护窗口完成。
- **状态**：正式 Peter 正在运行；Lan 可做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending）。

## P3-3 取消隐藏 + 去标签/domain 依赖 + 页面兼容（已合并 #444，已随 P3-4 部署）

- **代码**：#444 已合并于 `e7e7992`；隐藏机制、旧 tags/domain 页面依赖、自主删除审批与 surprise 页面隐藏均已完成。
- **正式状态**：已随 P3-4 维护窗口部署；启动时已执行旧隐藏行迁移，正式 Peter 当前运行 P3-4。
- **测试**：P3-3 9 项、隐藏/召回/判官/原生工具/事件/页面目标套件通过；本地全量 2204 项，20 failures / 2 errors 为既有 maintenance/冷备份环境依赖。

## P3-4 维护窗口数据清理 + 物理卸载 + 最终验收（已完成，P3 结束）

- **正式状态**：P3-4 已完成；正式 Peter 已重启并运行 P3-4（main 160b0ec），当前健康、页面/事件/记忆 API 正常。
- **清理结果**：正式 apply 删除 39 条 tombstone、10 条 letters/plans 重复行、24 个文件目标；6 条旧 plan 的完整行归档到 P3-4-maintenance/formal-archive/letters-plans-fe54030225fc2b36.json；Ombre 运行目录/进程/18011 已移除，安装包已归档。
- **数据**：正式库 240 memories / 234 versions / 240 vectors / 75 events / 76 stages / 593 event_ledger（含启动后正常 dream 新增） / 1 state / 120 relations / 24 sources / 6 quotes / 50 tombstones；正常 memories 与冷备逐项 P6 对比 mismatch 0；quick_check/digest ok。
- **备份/恢复**：维护前冷备 5428 文件 verify 通过；恢复演练 5427 文件通过；清理后 vault 导出 240 memories、Brain 备份/恢复副本 quick_check/digest ok。
- **脚本**：main 160b0ec 脚本与 #447 已验证版一致（sha256 97d5de...）；正式执行使用本地确定性修复版（sha256 d4e19e...）；确定性排序补丁待下一次开发 PR 同步，不单独开 PR。
- **文档**：最终验收报告 P3-4-maintenance/P3-4-final-acceptance-20261125.md。
- **未做**：未启动 P4；未扩大清理范围；未改权限/预算/模型配置。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；全新实例首次初始化；CodeRabbit 5 条复审意见已修。
- 已随 P3-2/P3-4 维护窗口部署；当前正式 Peter 运行 P3-4 代码。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口已完成更新/重启/技术验收；正式库 digest ok、0 tombstone、页面/事件 API 正常；P2 正式体验已由 Lan 确认通过。

## Lan 验收记录

- 2026-11-25：Lan 确认 P2 正式体验 + P3-0 技术验收通过；P3-1/P3-2/P3-3/P3-4 已合并并完成正式维护部署。
- 待 Lan 在正式 Peter 上做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending），确认无 18011/无 Ombre 子进程。

## 下一步

1. P3 已完成，正式 Peter 运行 P3-4；等下一次开发工单一起提交 STATUS/HANDOFF 和确定性补丁。
2. P4 未启动。
