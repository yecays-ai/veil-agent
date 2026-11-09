# 工单 323：状态对齐与工程规则

## 目标

STATUS/HANDOFF 如实区分开发侧验收与 Lan 体验验收；把 `integration-complete`（新增能力是否接入完整使用流程）加入开发规则。

## 当前状态

- 只改文档和规则文件，不改运行代码。
- `STATUS.md`/`HANDOFF.md` 按 2026-11-09 的日志与 Lan 实测更新；过期的“等待 PR #322”“PR 保持不合并”已移除。

## 验收步骤

1. 打开 `STATUS.md`，确认工单 320/321/322 的验收分别写明开发侧与 Lan 体验，且没有已合并却写“未合并”的内容。
2. 确认 `AGENTS.md`、PR 模板、`ticket` 技能引用了 `integration-complete`。
