# 交接

- 2026-10-22 Codex：工单 067，分支 `ticket/067-capability-notice-delivery`，未开 PR。旧实现已通过假模型复现：`pending_event` 是中文完整通知，`pending_event_model` 是英文简略工具清单，且 `Chat.send()` 在模型调用前清除通知，因此失败请求会丢失它。
- 已改为模型和页面都使用 `capability_event` 的同一英文正文；成功正常聊天产生有效回复后才消费。能力事件在 `capabilities.json` 按 UTC 日期持久化为 `capability_updates`，旧版未交付通知会迁移成当前工具及权限完整英文正文；自主活动不消费正常聊天通知。页面不自动显示事件，标题栏“能力记录”按钮从同一份记录读取，不存在用户版与 Peter 版。
- 已通过的假数据回归覆盖：两项 Lab 工具的正文一致性、失败后重试仅一次、自主活动不消费、重启后首次正常聊天交付、移除和权限修改、旧 manifest 迁移、记忆能力并发更新不丢记录、网页快照读取同一记录。仓库 `.venv` 的完整 `unittest discover -s tests -q` 与 `veil_agent.py --help` 已通过；真实 Windows 体验尚未执行。
- 下一窗口第一步：审查 diff、提交、推送、开 PR 并按 `pr-review` skill 请求 Cubic。
