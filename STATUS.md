# 当前状态

## P3-2 旧系统清理 + 事件附账本统一（本 PR，施工中）

- **目标**：Brain 成为唯一记忆/事件真源；事件证据/版本/修订/操作/投影/审查/dream 随 brain.db 一起备份、恢复、导出；移除 Ombre 代码/安装器/CI/旧配置/影子同步。
- **已完成**：新增 `event_ledger`/`event_state` 表；`BrainStore` 把 7 类事件附账本映射到 brain.db；一次性幂等迁移（先校验哈希，再归档原文件）；`brain_export` 导出完整事件生命周期；`brain_backup` 备份后校验 quick_check、digest 和计数；全新 Brain 初始化包含这些表。
- **旧系统清理**：删除 `brain_sync.py`、`ombre_resident.py`、`setup_memory.py`、Ombre 依赖锁/测试/一次性脚本；启动链和网页不再读取 Ombre 端口；过时 `[memory.brain]`、`ombre_port`、`ombre_model`、`ombre_auto_merge` 配置和检索 backend 开关已移除；setup/update/CI 不再安装或测试 Ombre。
- **兼容/安全**：离线导出仍保留旧格式（历史回退），不作为运行时后端；P6 认知字段未删。迁移按行保留原始顺序与重复记录，逐文件单事务 + import 标记，重试不重复；备份会重新计算 canonical digest、恢复前完整校验、停服/锁/WAL 检查、临时文件 + fsync + 原子替换，并保留 safety copy/export；恢复旧备份会把备份后新增的 memory/事件/附账本合并回 live 库。
- **复审修复**：Tests workflow 的重复/空 env 已修（四平台矩阵保留）；新增全七类账本 + 状态、重复行/顺序、迁移中断、重复启动、提交后归档中断、备份 digest/原子/WAL/新增数据保留测试。
- **测试**：`tests/test_p3_2_event_ledger.py` 16 项；P3-1 12 项、native/authority/chat/recall/event/brain/web/prompt 等目标套件通过；隔离副本完成实际迁移、启动、事件读写、导出、备份恢复和再次启动；本地全量排除外部 OpenCode 环境后 2192 项通过，剩余 22 项为既有 maintenance/冷备份环境依赖（基线同类）。
- **未做**：未部署正式环境；未在正式数据上执行迁移；未合并 PR；未做 P3-4 不可逆清理。CI 以 PR 结果为准。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；全新实例首次初始化；CodeRabbit 5 条复审意见已修。
- 合并后未自动部署；正式 Peter 仍按 P2 方式运行，待维护窗口更新。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口已完成更新/重启/技术验收；正式库 digest ok、0 tombstone、页面/事件 API 正常；P2 正式体验已由 Lan 确认通过。

## Lan 验收记录

- 2026-11-25：Lan 确认 P2 正式体验 + P3-0 技术验收通过；P3-1 已合并。
- 待 P3-1/P3-2 合并部署后：Lan 在正式 Peter 上做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending），确认无 18011/无 Ombre 子进程。

## 下一步

1. P3-2 PR 复审与 CI；不自行合并。
2. 合并后按维护窗口部署：首次启动迁移事件附账本、确认页面/导出/备份恢复，观察 1–2 天。
3. P3-3 页面/隐藏/domain；P3-4 不可逆清理需 Lan 维护窗口授权。
