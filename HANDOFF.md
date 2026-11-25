# HANDOFF

## P3-2 旧系统清理 + 事件附账本统一（2026-11-25，本 PR，施工中）

- 已完成：`event_ledger`/`event_state` schema；BrainStore 7 类事件附账本映射；幂等迁移 + 原文件只读归档；导出完整事件生命周期；备份后 quick_check/digest/计数校验；全新 Brain 初始化含新表。
- 已删除：`brain_sync.py`、`ombre_resident.py`、`setup_memory.py`、Ombre 锁/依赖/测试/一次性脚本；setup/update/CI 不再安装/测试 Ombre；`[memory.brain]`、`ombre_port`、`ombre_model`、`ombre_auto_merge` 和检索 backend 开关已移除。
- 测试：新增 `tests/test_p3_2_event_ledger.py`；目标套件通过；本地全量排除外部 OpenCode 环境后 2184 项通过，剩余 22 项为既有 maintenance/冷备份环境依赖。
- 未做：未部署正式环境；未在正式数据执行迁移；未合并；未做 P3-4 不可逆清理。
- 下一步第一步：等 PR 复审/合并；合并后在维护窗口部署，首次启动自动迁移事件附账本并验证页面/导出/备份恢复。
- 注意：正式 Peter 仍是 P2 运行状态；正式数据迁移前应有备份；测试只用新增专用测试记忆。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；首次初始化/缺库拒建/损坏库不替换；CI 全绿。
- 合并后未自动部署；正式 Peter 仍按 P2 方式运行，待维护窗口更新。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口完成更新/重启/技术验收；P2 正式体验已由 Lan 确认通过。

## P2 正式接管（2026-11-25）

- Brain 唯一写入权威、shadow 关闭、retrieval=brain；Ombre 18011 仍运行仅作临时回退。
- 回退：按 `docs/tickets/p2/P2-formal-cutover-report.md` 第 6 节，不要用旧备份直接覆盖当前 Brain。
