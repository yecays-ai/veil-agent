# P2-C 自审（隔离，2026-11-24）

## 已做

- `tag_governance.py`：审计、可逆清理（原 tags 存 `tag_migration`）、七类+未分类的确定性映射、verify、restore。
- 在正式 `brain.db` 的隔离副本上实际执行：
  - 清理前 285 条记忆、2912 次标签、2114 个不同标签、1815 个一次性。
  - 清理后 `with_tags=0`，正文/ID/来源/事件/版本未改。
  - 分类分布：experience 69、work 20、commitment 6、daily 4、preference 3、emotion 2、relationship 1、
    **unclassified 180（63.2%）**。
- 七类冲突/未分类验证说明：现有自由标签是跨类实体词，确定性映射只能覆盖 36.8%；**七类不保留为正式
  页面分类**，保留 structured 字段 + 语义检索，tags 只读历史。
- 恢复测试：`restore_tags` 可把原 tags 写回。

## 未做

- 未在正式库执行清理/迁移（只隔离副本）。
- 页面筛选/统计契约（P2-B 完成后页面工单处理）；145 条旧记忆只核实，未盲目归组。
- 未处理 Ombre 落盘 domain[0] 目录影响；P2-B 回退必须覆盖本单治理后数据。

结论：P2-C 的标签清理工具与隔离执行完成；分类方案结论是**不保留固定七类**。正式清理待 Lan 授权，
不在本单擅自执行。
