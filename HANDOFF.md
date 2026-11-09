# HANDOFF

## 当前工单 317（2026-11-09 08:20 +08）

- 分支 `ticket/317-remove-claude-code`，PR #316：https://github.com/yecays-ai/veil-agent/pull/316。开发在 `.agents/worktrees/ticket-317-remove-claude-code`，正式运行目录未动；没有修改或重新测试工单 315 的验收工作区。
- 工单 316（PR #315）已合并为 `b70ddaa`。本分支已同步该提交，解决 `STATUS.md`、`HANDOFF.md` 冲突；`veil_agent.py` 自动合并，已分别核对 316 的维护任务/工作台代码与 317 的 CC 移除。
- CC 撤除已完成：删除 `claude_agent_channel.py`、`claude_agent_bridge.py`、`scripts/claude_agent_smoke.py`、两个专属测试、`experiments/cc-bridge/`、`experiments/cc-jail/`；`veil_agent.py` 去掉 CC 导入、provider 配置、聊天执行器、成本写入、空回复、工作台、后台模型和委派成本分支；`model_settings.py` 去掉 CC 用途限制/密钥状态并加旧 `models.json` 一次性迁移；`character.toml` 删除三个 CC provider；桌面端删除 CC 筛选和提示。
- 审查修复已完成：无聊天模型时设置页可保存首个接口/模型；缺少 `providers` 表、provider 无效或只剩 CC provider 时可恢复启动；缺模型/Key 改为可恢复的 `RequestNotSent`，聊天循环和桌面服务不退出；带完整 API 字段的旧 `claude_agent` provider 不注册。跨进程 `models.json` 迁移/保存竞态未修改，理由见 PR。
- 合并后工作台回归：`test_executor_opencode` 100 项通过（2 项跳过）、`test_maintenance` 43 项、`test_workbench_switch` 2 项、`test_turn_stop` 7 项通过。其他受影响：model_settings 35、chat 89、web 67、tools 73（1 跳过）、autonomy 40、background 19、glance_model 6、delegate 9、mcp_services 23、tool_groups 10、tool_aliases 11、long_tasks 23、free_reply 31、cache_order 8、capability_transactions 7 项通过；Python 编译和 `node --check desktop/chat.js` 通过。
- 下一个窗口第一步：推送当前合并提交和文档，等 PR #316 最新 CI 全绿，确认必需检查和实际分支保护；满足后由 agent 合并。合并后按 `STATUS.md` 请 Lan 验收，不再开新 PR。
- 未验证：真实付费 API、真实 OpenCode 交互授权/停止、Windows 桌面实际点击；不能把自动测试当作桌面体验验收。
- 数据安全：迁移只处理确认过的 CC 内置 provider 引用，先写 `models.json.cc-removal-backup` 再原子替换；历史 `claude-agent-costs.jsonl`、聊天和记忆数据不删除、不改写。不要卸载或修改本机的 Claude Code / Claude Desktop。
