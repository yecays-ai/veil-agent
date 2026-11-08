# 当前状态

更新：2026-11-09 · agent（工单 317）。

## 当前目标

完成 Claude Code 专用接入撤除：模型设置不再出现 CC 模型和通道，正式运行不再查找、启动或管理 Claude CLI，同时保留 API 聊天、工具、记忆、OpenCode 工作台和五类独立选模。

## 现状

- 工单 314 已合并（PR #314，合并提交 `b7847ca`）；Lan 的 Windows 桌面体验验收仍未记录。
- 工单 316 在 PR #315（草稿）上，代码和自动检查已完成，等待 Lan 验收；本单先不合并且不覆盖它的改动。
- 工单 317 在 `ticket/317-remove-claude-code` 上开发，基于 `main` 的 `b7847ca`，PR #316（草稿）：https://github.com/yecays-ai/veil-agent/pull/316。已删除 CC 独立执行器、MCP 桥、Claude CLI 发现、后台子进程、专属实验、冒烟脚本和专属测试；`veil_agent.py`、`model_settings.py`、`characters/peter/character.toml` 与桌面端已完成定向删除。
- 旧 `data/<角色>/models.json` 中已确认的 CC 内置模型引用会在启动时迁移：先保留 `models.json.cc-removal-backup`，再原子替换。聊天和记忆后台优先恢复已填写 Key 的 API 模型；所有 API Key 都缺失时不会把模型标成可用，仍可启动并提示填写 Key；连普通 API 模型都没有时以显式“未选择模型”状态启动，仍能打开设置。自主活动和瞄一眼恢复默认选择，委派清空并提示重选；其他用途、自定义模型、接口、API Key、价格和费用上限不变。再次启动不重复迁移，也不覆盖用户之后的选模。
- 自动检查：model_settings 32 项（含默认 Key 缺失、默认模型不可用、只有 CC provider、无模型启动、迁移幂等/备份边界）、glance_model 6 项、chat 88 项、web 67 项、tools 73 项（1 跳过）、delegate 9 项、autonomy 40 项、background 19 项、glance 3 项、executor_opencode 67 项（2 跳过）、maintenance 15 项、mcp_services 23 项、tool_groups 10 项、tool_aliases 11 项、long_tasks 23 项、turn_stop 7 项、free_reply 31 项、cache_order 8 项、capability_transactions 7 项通过；Python 编译和 `node --check desktop/chat.js` 通过。
- 未完成：真实付费 API、真实 OpenCode 工作台授权/停止、Windows 桌面实际点击未验证。PR #316 暂不合并；316 合并后还要解决冲突并重跑工作台回归。代码合并、自动检查通过、Windows 体验验收分别记录，不相互代替。

## 下一步

等 316（PR #315）合并；解决本分支 `STATUS.md`、`HANDOFF.md` 和 `veil_agent.py` 的冲突，重跑工作台回归（维护任务启动、执行、停止、取消、状态写回、资源清理）和受影响测试，然后请求 Cubic 审查并合并。合并后请 Lan 按下面步骤验收。

## Lan 的 Windows 体验验收

1. 重启 Peter，打开设置 → 模型：Claude Code 的 Sonnet、Haiku、Opus 和通道入口消失；普通 API 模型、自定义模型、接口、API Key、价格和费用上限还在。
2. 用现有 API 模型聊一句，并让它读一条记忆、存一条记忆；分别切换聊天、自主活动、记忆后台、瞄一眼、委派模型，重启后确认各自仍然保留。
3. 用非 CC API 模型跑一次只读委派任务；再打开 OpenCode 工作台读取、修改一个测试文件、执行一次测试并停止；确认任务栏和进程里没有 VEIL 启动的 Claude CLI。
