# 当前状态

## P3-2 旧系统清理 + 事件附账本统一（已合并 #443，已部署）

- **代码**：#443 合并于 `f63e58a`；正式目录已通过维护窗口更新到该提交。
- **正式部署**：2026-11-25 停服 → 完整冷备份（D:\Peter backup\VEIL-Agent-Backup-20261125T084139.218764Z-4688aa1f，verify 5427 文件）→ 更新 → 首次启动迁移 → 验收。事件附账本、events/stages、dream 状态已进入 brain.db；原文件归档到 `data/peter/archive/pre-p3-2-event-ledgers-20261125T164654`（只读，manifest 720 行）。
- **正式数据**：289 memories / 286 versions / 250 vectors / 75 events / 76 stages / 566 event_ledger / 1 state；quick_check ok、digest ok；与预迁移备份逐 ID 对比无缺失。页面/事件 API 正常，无 18011、无 Ombre。
- **回退**：完整冷备份 + 预迁移 Brain 备份；brain_backup 恢复会保留 safety copy/export 并合并备份后新增记录；未执行 P3-4。
- **状态**：正式 Peter 正在运行；Lan 可做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending）。

## P3-3 取消隐藏 + 去标签/domain 依赖 + 页面兼容（施工中）

- **目标**：隐藏机制取消；唯一旧 hidden/archived 行恢复为正常可见/可召回；归档生命周期保留；页面改为 全部/最近/经历/已归档；页面/搜索/召回/判官不再依赖旧 tags/domain；数据列与 P6 字段保留。
- **已完成**：启动迁移 `unhide_legacy_rows`（只改可见性/类型，正文/why/P6 不动）；BrainAuthority、BrainRetrieval、RecallJudge、Memory、Native tools、BrainRead 面板移除 hidden/dont_surface 行为；删除 dont_surface 工具参数；关闭空事件改为归档而非隐藏；页面移除 us/peter/system/domain/tag 分组与搜索依赖。
- **复审补丁**：修复 `is_hidden(delete_pending)` 与自主删除审批的状态冲突——`process_pending_deletions` 改用 archived/deleted 生命周期判断，pending 请求会正常进入 Lan 审批；同意删除、拒绝后恢复可见/召回均有测试。旧 surprise 标签只保留历史数据，不再隐藏页面正文/why/标题或禁止编辑。
- **未做**：不逐条改记忆内容、不自动删除/合并、不执行 P3-4；正式部署待本 PR 合并和维护窗口。
- **测试**：新增 `tests/test_p3_3_visibility.py`（6 项）；隐藏/召回/判官/原生工具/事件/页面目标套件通过；本地全量排除外部 OpenCode 环境 2202 项，20 failures / 2 errors 均为既有 maintenance 假 git/gh 与冷备份 desktop-data 环境依赖（与 P3-2 基线同类），最终以 PR CI 为准。
- **验收步骤**：记忆页 全部/最近/经历/已归档可用；旧 hidden 行正常显示并可召回；旧标签/domain 不再出现在页面/搜索；归档、编辑、删除、事件关联仍正常；P6 数据可查。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；全新实例首次初始化；CodeRabbit 5 条复审意见已修。
- 已随 P3-2 正式维护窗口部署；当前正式 Peter 运行 P3-2 代码。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口已完成更新/重启/技术验收；正式库 digest ok、0 tombstone、页面/事件 API 正常；P2 正式体验已由 Lan 确认通过。

## Lan 验收记录

- 2026-11-25：Lan 确认 P2 正式体验 + P3-0 技术验收通过；P3-1/P3-2 已合并并完成正式维护部署。
- 待 P3-1/P3-2 合并部署后：Lan 在正式 Peter 上做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending），确认无 18011/无 Ombre 子进程。

## 下一步

1. P3-2 PR 复审与 CI；不自行合并。
2. 合并后按维护窗口部署：首次启动迁移事件附账本、确认页面/导出/备份恢复，观察 1–2 天。
3. P3-3 页面/隐藏/domain；P3-4 不可逆清理需 Lan 维护窗口授权。
