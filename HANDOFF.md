# HANDOFF

## P3-4 维护窗口数据清理 + 物理卸载 + 最终验收（2026-11-25，施工中）

- 已完成：新增手动清理工具 `scripts/p3_4_cleanup.py`；plan 校验冷备覆盖（逐文件 sha256、目录成员、brain.db 哈希）、文件/目录内容指纹、独占计划文件，apply 需相同 plan + 确认词 + 停服/写锁/网页端口检查；删除事务内确认只处理批准 ID，提交后支持 VACUUM/文件清理中断恢复；清理已删除/墓碑内容、letters/plans DB 重复行、旧 embeddings/JSONL/vault/备份副本、Ombre 运行目录；清理前后逐行对账，共享事件和 P6 不动。
- 已完成：移除遗留 Ombre 入口源文件 `ombre_entry.py`/`ombre_host.py` 和 prompt allowlist 引用；新增隔离测试 `tests/test_p3_4_cleanup.py`（P6 保留、墓碑/letters-plans 清理、文件保护、错误计划拒绝）和 `docs/p3-4-maintenance-runbook.md`。
- 未做：**未在正式数据执行任何清理**；未创建正式 plan、未删除正式文件、未物理卸载正式 Ombre。P3-4 必须在 Lan 维护窗口确认名单、完成冷备 + 恢复演练后执行。
- 下一步：PR 复审；合并后在维护窗口按 runbook 先部署 P3-3，再 plan 复查/apply/启动/导出/恢复复验；P3-4 完成记录只在验收后写。
- 正式状态：正式 Peter 仍运行 P3-2/f63e58a；P3-3 已合并未部署。

## P3-3 取消隐藏 + 去标签/domain 依赖 + 页面兼容（已合并 #444，未部署）

- 隐藏机制/旧 tags/domain 页面依赖已移除；旧 hidden 行启动迁移；自主删除审批与 surprise 页面隐藏已修。
- 未部署；P3-4 维护窗口会先部署 P3-3。

## P3-2 旧系统清理 + 事件附账本统一（已合并 #443，已部署）

- 事件附账本/状态进入 brain.db；正式维护已完成迁移和验收；未执行 P3-4。

## P3-1 启动解耦 + Brain-only 读路径（已合并 #442）

- 启动不再依赖 Ombre；Brain-only 读写/召回/页面/事件；修复 pinned/importance、自主删除、判官、事件缓存、BM25 降级；首次初始化/缺库拒建/损坏库不替换；CI 全绿。
- 已随 P3-2 维护窗口部署；正式 Peter 当前运行 P3-2 代码。

## P3-0 配置持久化与前置准备（已合并 #441）

- #441 已合并；维护窗口完成更新/重启/技术验收；P2 正式体验已由 Lan 确认通过。

## P2 正式接管（2026-11-25）

- Brain 唯一写入权威、shadow 关闭、retrieval=brain；Ombre 18011 仍运行仅作临时回退。
- 回退：按 `docs/tickets/p2/P2-formal-cutover-report.md` 第 6 节，不要用旧备份直接覆盖当前 Brain。
