# 当前状态

## P3-2 旧系统清理 + 事件附账本统一（已合并 #443，已部署）

- **代码**：#443 合并于 `f63e58a`；正式目录已通过维护窗口更新到该提交。
- **正式部署**：2026-11-25 停服 → 完整冷备份（D:\Peter backup\VEIL-Agent-Backup-20261125T084139.218764Z-4688aa1f，verify 5427 文件）→ 更新 → 首次启动迁移 → 验收。事件附账本、events/stages、dream 状态已进入 brain.db；原文件归档到 `data/peter/archive/pre-p3-2-event-ledgers-20261125T164654`（只读，manifest 720 行）。
- **正式数据**：289 memories / 286 versions / 250 vectors / 75 events / 76 stages / 566 event_ledger / 1 state；quick_check ok、digest ok；与预迁移备份逐 ID 对比无缺失。页面/事件 API 正常，无 18011、无 Ombre。
- **回退**：完整冷备份 + 预迁移 Brain 备份；brain_backup 恢复会保留 safety copy/export 并合并备份后新增记录；未执行 P3-4。
- **状态**：正式 Peter 正在运行；Lan 可做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending）。

## P3-3 取消隐藏 + 去标签/domain 依赖 + 页面兼容（已合并 #444，未部署）

- **代码**：#444 已合并于 `e7e7992`；隐藏机制、旧 tags/domain 页面依赖、自主删除审批与 surprise 页面隐藏均已完成。
- **正式状态**：尚未执行 P3-3 维护部署；正式 Peter 仍运行 P3-2/f63e58a，第一次 P3-3 启动会执行 `unhide_legacy_rows`。
- **测试**：P3-3 9 项、隐藏/召回/判官/原生工具/事件/页面目标套件通过；本地全量 2204 项，20 failures / 2 errors 为既有 maintenance/冷备份环境依赖。

## P3-4 维护窗口数据清理 + 物理卸载 + 最终验收（施工中）

- **目标**：维护窗口内对已确认名单执行不可逆清理：墓碑正文/raw/版本/向量、letters/plans DB 重复行、旧 JSONL/vault/embeddings、Ombre 运行目录/旧备份副本；只保留最小防复活标记和 P6 数据；最后复验导出/备份恢复。
- **已完成**：新增手动清理工具 `scripts/p3_4_cleanup.py`（plan/apply，备份清单 + DB 哈希绑定，停服/写锁/网页端口检查，确认词）；移除遗留 Ombre 入口源文件；新增隔离测试；运行手册 `docs/p3-4-maintenance-runbook.md`。
- **未做**：未在正式数据执行任何清理；未创建正式计划/未删除正式文件；需 Lan 在维护窗口确认名单、完成冷备和恢复演练后执行。P3-4 完成记录只能在维护窗口验收后写。
- **下一步**：PR 复审；合并后在维护窗口执行 plan → 复查 → apply → 启动/导出/恢复复验，再更新 STATUS 为 P3 完成。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；全新实例首次初始化；CodeRabbit 5 条复审意见已修。
- 已随 P3-2 正式维护窗口部署；当前正式 Peter 运行 P3-2 代码。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口已完成更新/重启/技术验收；正式库 digest ok、0 tombstone、页面/事件 API 正常；P2 正式体验已由 Lan 确认通过。

## Lan 验收记录

- 2026-11-25：Lan 确认 P2 正式体验 + P3-0 技术验收通过；P3-1/P3-2 已合并并完成正式维护部署。
- 待 P3-1/P3-2 合并部署后：Lan 在正式 Peter 上做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending），确认无 18011/无 Ombre 子进程。

## 下一步

1. P3-4 PR 复审；不自行合并。
2. 合并后按维护窗口部署 P3-3，再执行 P3-4 冷备/恢复演练/名单复查/清理/最终验收。
3. P3-4 不可逆清理必须由 Lan 在维护窗口确认名单后执行；不在开发窗口操作正式数据。
