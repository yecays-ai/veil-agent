# HANDOFF

## P3-3 取消隐藏 + 去标签/domain 依赖 + 页面兼容（2026-11-25，施工中）

- 已完成：`unhide_legacy_rows` 启动迁移（旧 hidden/dont_surface 行恢复为 normal dynamic，正文/why/P6 不改，归档行保留）；移除 BrainAuthority/BrainRetrieval/RecallJudge/Memory/Native/BrainRead 面板的 hidden/dont_surface 过滤和工具参数；空事件改为归档；页面分组改为 全部/最近/经历/已归档，页面搜索/事实行去掉 domain/tag，移除 us/peter/system 分组依赖。
- 测试：新增 `tests/test_p3_3_visibility.py` 6 项；更新 hidden/召回/判官/原生工具/事件/分页/页面目标测试；本地全量 2202 项，20 failures / 2 errors 为既有环境依赖，目标套件通过；最终以 PR CI 为准。
- 未做：不逐条改记忆内容、不自动删除/合并、不执行 P3-4；P3-3 尚未合并/部署。
- 下一步第一步：等全量测试和 PR 复审；合并后按维护窗口部署，启动时自动 unhide；正式验收确认页面/召回/判官/编辑/归档/删除/事件关联一致。
- 注意：正式 Peter 已运行 P3-2（f63e58a）；不要在正式目录开发或直接改正式 brain.db。

## P3-2 旧系统清理 + 事件附账本统一（已合并 #443，已部署）

- 已完成：事件附账本/状态进入 brain.db；迁移幂等且保留重复/顺序，原文件只读归档；导出/备份/恢复覆盖事件生命周期；Ombre 代码/安装/CI/旧配置清理。
- 正式维护：2026-11-25 完整冷备份 + 预迁移 Brain 备份；更新到 f63e58a；首次启动迁移 720 行归档；正式数据逐 ID 对比无缺失；页面/事件 API 正常，无 18011/Ombre；未执行 P3-4。
- 回退：完整冷备份 + 预迁移 Brain 备份；不要用旧备份直接覆盖 P3-2 后新增数据。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；首次初始化/缺库拒建/损坏库不替换；CI 全绿。
- 已随 P3-2 维护窗口部署；正式 Peter 当前运行 P3-2 代码。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口完成更新/重启/技术验收；P2 正式体验已由 Lan 确认通过。

## P2 正式接管（2026-11-25）

- Brain 唯一写入权威、shadow 关闭、retrieval=brain；Ombre 18011 仍运行仅作临时回退。
- 回退：按 `docs/tickets/p2/P2-formal-cutover-report.md` 第 6 节，不要用旧备份直接覆盖当前 Brain。
