# HANDOFF

## 当前工单 317（2026-11-09）

- 分支 `ticket/317-remove-claude-code`，从 `main` 的 `b7847ca` 建立；PR #316（草稿）：https://github.com/yecays-ai/veil-agent/pull/316。开发在 `.agents/worktrees/ticket-317-remove-claude-code`，正式运行目录未动。
- 已完成：删除 `claude_agent_channel.py`、`claude_agent_bridge.py`、`scripts/claude_agent_smoke.py`、两个专属测试、`experiments/cc-bridge/`、`experiments/cc-jail/`；`veil_agent.py` 去掉 CC 导入、provider 配置、聊天执行器、成本写入、空回复、工作台、后台模型和委派成本分支；`model_settings.py` 去掉 CC 用途限制/密钥状态，并加旧 `models.json` 的一次性安全迁移；`character.toml` 删除三个 CC provider；桌面端删除 CC 筛选和提示并显示迁移提示。
- 自动检查：model_settings 26 项、glance_model 6 项、chat 88 项、web 67 项、tools 73 项（1 跳过）、delegate 9 项、autonomy 40 项、background 19 项、glance 3 项、executor_opencode 67 项（2 跳过）、maintenance 15 项、mcp_services 23 项、tool_groups 10 项、tool_aliases 11 项、long_tasks 23 项、turn_stop 7 项、free_reply 31 项、cache_order 8 项、capability_transactions 7 项通过；Python 编译和 `node --check desktop/chat.js` 通过。
- 卡点：316（PR #315）仍是草稿，等待 Lan 验收；本单 PR 暂不合并。316 合并后需要解决 `STATUS.md`、`HANDOFF.md` 和 `veil_agent.py` 的冲突。
- 下一个窗口第一步：拉取最新 `main`，确认 316 已合并；把最新 `main` 合入本分支解决冲突，重跑受影响测试，请求 Cubic 审查，满足门禁后合并，再请 Lan 按 `STATUS.md` 验收。
- 未验证：真实付费 API、真实 OpenCode 工作台授权/停止、Windows 桌面实际点击；不能把自动测试当作桌面体验验收。
- 数据安全：迁移只处理确认过的 CC 内置 provider 引用，先写 `models.json.cc-removal-backup` 再原子替换；历史 `claude-agent-costs.jsonl` 和聊天/记忆数据不删除、不改写。不要卸载或修改本机的 Claude Code / Claude Desktop。
