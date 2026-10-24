# 当前状态

更新：2026-10-24 UTC · Codex。

## 当前目标与现状

- Reliability 五阶段已完成；运行期监测与安全恢复退出条件满足。
- PR #126（工单 082）已合并；Tests、Windows desktop、Dependency security 均通过，桌面体验验收已完成。
- PR #127（工单 083）已合并；聊天恢复、上下文预算校准和 DSML 原文拦截已进入 `main`，相关 CI 与桌面体验验收已完成。
- PR #128（工单 084）已合并，提交 `283f3be`，同步了 Reliability 与已完成验收状态。
- PR #130（工单 086）已合并，提交 `bad8a42`；Tests 与 Dependency security 均通过。Chat/Memory 多 assistant 轮次行为已修正；工单 086 的手动 Windows 场景仍按下方步骤验收。
- 工单 087「核心模块共享基础类型与错误定义」在独立分支 `ticket/087-core-import-types`。共享错误、时间戳 helper 和自主唤醒哨兵移入低层 `agent_types.py`，核心模块不再为这些定义反向导入 `veil_agent.py`；本次不改业务策略或用户数据。

## 下一步

完成工单 087 的改动复核后，只开一个 PR 并等待该 head 的自动检查与审查；合并后请 Lan 按下方步骤确认运行行为。

## Lan 的体验验收

1. 更新并启动 VEIL，确认桌面正常打开，发送普通消息后 Peter 能正常回复。
2. 使用一次文件读取和一次记忆查询，确认工具和记忆仍能正常工作。

