# 101 Peter 把自己做的东西发给 Lan

## 目标

Peter 可以把自己在 Lab 做好的网页、图片或文本作为附件发给 Lan；聊天记录重启后仍能打开，回复邮件时附件也随邮件发送。

## 当前状态

`main` 上的 `send_message` 只支持文字和语音，Lab 文件由 `tools_agent.py` 管理，桌面已有 `web_agent.Hub` 的媒体目录、`index.jsonl` 和受令牌保护的 `/media/{id}` 路由，但消息记录与邮件发送还没有关联附件。本分支已补上附件传递、历史恢复和桌面展示；Lan 已确认本地预览。PR #154 的 CodeRabbit 审查确认了邮件附件上限和删除/撤回清理两处问题，当前分支已修复并增加回归测试；后续 CI 状态以 PR 最新提交为准。PR 保持打开，不自动合并。合并后再做 Windows 实际体验验收。

复用方案：附件快照和历史索引复用 `web_agent.Hub` 的 `media_root/index.jsonl` 及其认证媒体路由，不另建存储或调度机制；邮件使用 Python 标准库 `email.message.EmailMessage.add_attachment()`（[CPython v3.12.10 源码](https://github.com/python/cpython/blob/v3.12.10/Lib/email/message.py#L1192-L1193)），不增加第三方依赖。允许类型、5 MB 限额和 Lab 路径检查由 `attachment_utils.py`、`tools_agent.FilesystemTools` 执行；消息及邮件分别由 `veil_agent.Chat`、`web_agent.Hub`、`email_channel.EmailChannel` 和 `away_bridge.AwayBridge` 处理，桌面展示位于 `desktop/chat.js` 与 `desktop/main.cjs`。

## 验收步骤

1. 让 Peter 在 Lab 做一个小网页或图片并发给 Lan：聊天里出现文件卡片，图片可直接看、网页可在默认浏览器打开、文本可在聊天窗口展开查看。
2. 重启 VEIL 后打开历史附件，确认仍可用；再让 Peter 回复一封邮件并附上 Lab 文件，确认邮件收到了附件。
