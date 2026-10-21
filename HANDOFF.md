# 交接

- 2026-10-21：工单 037 从最新 main `96a91d9` 开始，分支 `ticket/037-memory-proactive-restart`。已在 Extract/Native 两种模式用虚构 JSONL 复现：每个用户轮次有多条助手消息时，精确 `len == 2` 的过滤会让已持久化 cursor 大于当前轮次数。已将统计修为每轮仅取首条助手回复，并添加直接读写持久化文件、重新初始化、检查聊天原文字节未变的回归测试。目标用例及记忆相关测试通过（`test_memory.py` 40 项、`test_native_memory.py` 45 项）。下一步检查 PR 的 CI 与评审；合并后 Lan 按 `docs/tickets/037-memory-proactive-restart.md` 验收。
- 2026-10-21 Claude：工单 036 Peter 身份分层。身份与行动方式都在本机 `persona/peter.md`（v3 在项目共享文件夹 `peter-companion/peter-identity-v3.md`，不进公开仓库）；权限仍由程序给出，成长经历留给 Ombre。代码只改了程序事实里的 “terminal text chat”。Ombre 工具定义的 token 占用未测（本机装不上 Ombre）。Windows 体验待 Lan 验收。
- 2026-10-21 Claude：去掉「新对话」写死的英文提示，按钮改发 `/new`；`/new-en` 仍可输入但等同 `/new`。
- 工单 034（PR #66）、035（PR #70）已合并；Windows 体验待 Lan 验收。
