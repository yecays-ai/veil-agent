# HANDOFF

## 当前工单 323（2026-11-09）：状态对齐与工程规则

- 范围：只改文档和规则文件。更新 `STATUS.md`、`HANDOFF.md`，加入 `integration-complete` 规则（`AGENTS.md`、PR 模板、`ticket` 与 `integration-complete` 技能）。不改运行代码。
- 目的：之前的 STATUS 把开发侧已完成的验收也写成“待 Lan”，并保留已过期的“等待 PR #322”。现在开发验收与 Lan 体验分开写。

## 工单 322 正式使用观察（2026-11-09，来自 `data/peter/tools.jsonl` 与 Lan 截图）

- 20:07 首次：主聊天旧 `windows_*` 微动作连续出现多次“结果不确定”（`windows_control_action`、`windows_manage` 超时），文字实际已写入记事本；20:08:45 才调用 `workbench`，20:08:58 返回 `1 steps, 0 tool calls`、原因“桌面任务已停止或结果不确定”。停止原因日志里无法区分：人工输入接管、取消标志、主聊天停止。该判断未验证。
- 20:14 第二次：Peter 用 `field_trip` 浏览器和 `windows_*` 完成读页、新标签写摘要、不保存；没有 `workbench` 调用。其间 `windows_launch`、`windows_manage`、`windows_control_action` 各出现一次“结果不确定”，靠重新观察绕过。
- 在允许的软件里普通输入无需审批是 2026-10-25 的决定；只有保存、发送、覆盖确认才问。所以往已打开文档输入不弹审批符合规则，不是缺陷。
- 两次都说明旧的主聊天桌面微动作入口仍被优先使用。是否在工作台开启时收起这些入口，待 Lan 决定（对应审计的“撤除已替代入口”）。

## 其他待办

- 工单 320 的三个缺口见 `STATUS.md`。
- 审计的“成果保存与安全续接”（工单三）未开始。
- 工单 318/319 无 Lan 真实体验证据。
