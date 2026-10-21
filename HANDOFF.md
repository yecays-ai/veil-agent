# 交接

- 2026-10-21 09:54 UTC Codex：工单 038 从 main `630b609` 开始，分支 `ticket/038-capability-transactions`，仅修 031-07。`capability_state.py` 提供按规范文件路径共享的进程内事务锁和同目录原子保存；Files 四个写入事务与 Native bind 共用。锁内不调用 Hub/审批/模型，网页仍为 Hub→能力锁顺序。旧版三个并发场景稳定失败；修复后能力、文件工具、记忆、自主、长期任务离线回归运行 168 项，167 通过、1 项跳过。全套本地在受限环境进入真实 Node/MCP 集成等待，已停止，不能称本地全套通过；完整矩阵以 CI 为准。下一步检查本单 PR 的最新 CI、正式小兔子审查及合并状态；合并后 Lan 按 STATUS 验收。未操作本机角色数据或付费接口。
- 2026-10-21 Claude：Peter 默认打开思考模式（`thinking = "enabled"`），避免更新 main 时覆盖 Lan 本机设置。证据：`data/costs.jsonl` 输出 token 在本机设置被覆盖后骤降，同期自主 hold 停止；重新打开后已恢复自主 hold。费用粗估每天多约 ¥0.5，未实测。
- 2026-10-21：工单 037 从最新 main `96a91d9` 开始，分支 `ticket/037-memory-proactive-restart`。已在 Extract/Native 两种模式用虚构 JSONL 复现：每个用户轮次有多条助手消息时，精确 `len == 2` 的过滤会让已持久化 cursor 大于当前轮次数。已将统计修为每轮仅取首条助手回复，并添加直接读写持久化文件、重新初始化、检查聊天原文字节未变的回归测试。目标用例及记忆相关测试通过（`test_memory.py` 40 项、`test_native_memory.py` 45 项）。PR #73 代码提交 `a66d351` 的 Windows/Linux × Python 3.11/3.12 CI 通过；已手动触发小兔子正式审查，完成且无须处理的意见。收尾同步 main `b846d08`，保留 Peter 思考模式配置及交接记录，037 代码与测试未变；最终提交 CI 已通过，PR #73 已合并为 main `630b609`；Lan 按 `docs/tickets/037-memory-proactive-restart.md` 验收。
- 后续窗口：先同步最新 main 并读 STATUS/HANDOFF，核对 038 的 PR 交付与 Lan 体验结果。037 的 Windows 体验仍未验收，不能以 CI 代替。
- 2026-10-21 Claude：工单 036 Peter 身份分层。身份与行动方式都在本机 `persona/peter.md`（v3 在项目共享文件夹 `peter-companion/peter-identity-v3.md`，不进公开仓库）；权限仍由程序给出，成长经历留给 Ombre。代码只改了程序事实里的 “terminal text chat”。Ombre 工具定义的 token 占用未测（本机装不上 Ombre）。Windows 体验待 Lan 验收。
- 2026-10-21 Claude：去掉「新对话」写死的英文提示，按钮改发 `/new`；`/new-en` 仍可输入但等同 `/new`。
- 工单 034（PR #66）、035（PR #70）已合并；Windows 体验待 Lan 验收。

