# 交接

更新：2026-10-21 · Codex · 工单 030 `ticket/030-autonomous-messages` 施工中

## 做到哪

- 已从 GitHub 核实最新 main `74f59b8b93d8b5cd65537e6e6a070208118891fb`；工单 028 PR #59 和工单 029 PR #60 均已合并。施工分支从该最新 main 建立，独立于已合并的 029 语音改动。
- 复用调查记录在 `docs/tickets/030-autonomous-messages.md`。Fount 的 wake scheduler 用运行中标记和序号避免并发/漏触发，async-task registry 用任务 ID 与 generation ID 管理任务归属；仅参考并发和幂等思路，不复制代码或引入依赖。
- 实现自主调用现有 `send_message`，写入角色现有 `chat.jsonl` 并通过现有网页 assistant 事件显示；最终自主总结仍只写自主活动记录。每条消息保存唤醒 ID 和工具调用 ID，持久计数限制每次最多三条；消息持久化、用户提交、暂停与退出通过 Hub 同一同步门排序。
- 定向验证：`test_autonomy.py`、`test_tools.py`、`test_chat.py` 合计运行 94 项，92 项通过、2 项可选集成跳过，并通过 `py_compile`。`test_web.py` 本环境未能跑通：沙箱拒绝本机回环 socket，现有 HTTP 用例均受影响。Windows 网页体验未验收。

## 下一步

1. 完成变更自审与 `git diff --check`，将实现、工单和状态文档提交至 `ticket/030-autonomous-messages` 并创建一个 PR。
2. 按用户要求把 PR 交 Claude 审查，不扩展功能或自行合并。
3. Lan 按 `STATUS.md` 的工单 030 步骤完成真实 Windows 验收；自动化检查不替代网页体验验收。

## 注意

- 未新增手机推送、通知、新队列、定时器、模型请求、权限或预算；未改长期任务规则、Ombre、记忆、lab 权限和网页界面。
- 只在虚构数据和假模型中验证；未读取或修改真实聊天、记忆、人设、lab 或账本。

