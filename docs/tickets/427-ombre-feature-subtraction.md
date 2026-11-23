# 工单 427 · 下线 Ombre 的功能层，计划和信件搬进 VEIL

## 背景

记忆库审计（2026-11-23）量出来的事实：Ombre 的 16 个工具里，信件三件套、`plan`、`pulse`、`I`、
`anchor`、`release` 要么零调用，要么已经被 VEIL 自己的工具顶掉；`feel` 的写入路径在 10-19 就删了，
只剩一条每轮都要做一次 embedding、却只有约 0.5% 轮次命中的只读通道，而且它绕过了召回判官。
Lan 的决定：这些能力不要了，计划改由共享日历承担，信件导出到 lab 后下线，库状态并入 `memory_catalog`。

## 目标

- 从工具箱、代码和提示词里删掉：`letter_write`、`letter_read`、`letter_lock_update`、`plan`、
  `pulse`、`I`、`anchor`、`release`，以及 `feel` 召回通道。
- 保留并只保留：`hold`、`breath_search`、`trace`、`dream`、`memory_read`、`memory_catalog`、
  `memory_history`、`memory_timeline`。
- `memory_catalog` 的第一行给出库状态（条数、事件数、归档数、隐藏数、最新写入、占用），替代 `pulse`。
- 计划以后活在共享日历里：`life_record` 允许 Peter 新建 `kind="plan"`；每轮上下文里的计划块从日历读。
- 桌面端不再提供「高级管理 · Ombre Dashboard」入口（Lan 从没用过 OB 自己的前端）。
- 旧对话里若还调这些工具，返回一句「这个能力现在在哪」，不报未知工具。

## 改法

- `memory_native.py`：工具表、描述、`plan_entries`、`_filter_hidden_i`、pulse 过滤全部删除；
  `library_stats()` 生成 `memory_catalog` 的 `stats` 字段。
- `memory_agent.py`：删 feel 检索与合并；`call` 的计费名单只留还在用的工具。
- `veil_agent.py`：`life_state` 的计划块只读日历；新增 `REMOVED_TOOL_HINTS`。
- `life_records.py`：允许创建/转换 `kind="plan"`（仍然不能带提醒或每日重复）。
- `tool_groups.py` / `tools_agent.py`：`memory_admin` 与权限表清理。
- `web_agent.py`：去掉 `memory_panel_url`、Ombre plan 合并与 `ombre-plan:` 更新分支。
- `desktop/`：去掉 dashboard 按钮、IPC 与 preload 暴露。
- 测试：删掉已下线能力的用例，补「dashboard 入口已不存在」与「日历可以建计划」。

## 边界

- Ombre 源码保持钉死未改（工单 125 的哈希校验）：删的是 VEIL 的工具暴露与调用，不是改上游。
  它的 `pulse`、`plan` 等实现仍在，只是不再给 Peter，也不再被 VEIL 调用。
- 本 PR 不动任何记忆数据；迁移与删除由工单 426 的脚本执行。
- 库状态里的「索引健康告警」不搬：等以后做记忆健康维护板块时一起设计。

## 验收（Lan 在 Windows 上做）

1. 记忆页仍然正常：最近/经历/我们俩/他自己/系统和能力/已归档，没有空分类。
2. 让 Peter「给我写封信」：他用写文件/放桌面/贴纸，不报错。
3. 问「我们之前有哪些计划」：他翻日历回答；日历里 5 条历史计划标着已完成、不响提醒。
4. 让他「看看记忆库有多少条」：`memory_catalog` 返回第一行统计，没有 `pulse`。
5. 桌面记忆页右上不再有 Ombre Dashboard 按钮。
6. 回归：存一条新记忆、自动想起、经历页、重启都正常。
