# 当前状态

更新：2026-11-09 08:20 (+08) · 工单 317（已同步已合并的工单 316）。

## 当前目标

完成 Claude Code 专用接入撤除：模型设置不再出现 CC 模型和通道，正式运行不再查找、启动或管理 Claude CLI，同时保留 API 聊天、工具、记忆、OpenCode 工作台和五类独立选模。

## 现状

- 工单 314 已合并（PR #314，合并提交 `b7847ca`）。
- 工单 316 已合并（PR #315，合并提交 `b70ddaa`）。Lan 的 Windows 体验验收已通过：隔离容器 workbench 修改 `app.py` 并运行测试成功，普通测试没有误弹审批卡；模拟 PR 交付、未授权合并保护、运行中停止、重启后保持停止均符合预期。真实 GitHub 写入和真实分支保护下的交付仍未验证（该单的残余验证项）。
- 工单 317 在 `ticket/317-remove-claude-code` 上开发，PR #316：https://github.com/yecays-ai/veil-agent/pull/316。已同步最新 `main`（`b70ddaa`），解决 `STATUS.md`、`HANDOFF.md` 冲突；`veil_agent.py` 自动合并后已按 316 的工作台/维护代码和本单的 CC 移除分别核对。
- 已删除 CC 独立执行器、MCP 桥、Claude CLI 发现、后台子进程、专属实验、冒烟脚本和专属测试；`veil_agent.py`、`model_settings.py`、`characters/peter/character.toml` 与桌面端已完成定向删除。OpenCode、维护任务、MCP、委派、费用和工具等共享基础设施保留。
- 旧 `data/<角色>/models.json` 中已确认的 CC 内置模型引用会在启动时迁移：先保留 `models.json.cc-removal-backup`，再原子替换。聊天和记忆后台优先恢复已填写 Key 的 API 模型；所有 API Key 都缺失时不会把模型标成可用，仍可启动并提示填写 Key；没有普通 API 模型时以显式“未选择模型”状态启动，仍能打开设置。自主活动和瞄一眼恢复默认选择，委派清空并提示重选；其他用途、自定义模型、接口、API Key、价格和费用上限不变。
- 审查修复已完成：无聊天模型时设置页仍能保存首个接口和模型；缺少 `providers` 表、provider 无效或只剩 CC provider 时跳过无效项并进入可恢复启动；缺模型/Key 按可恢复请求错误处理，聊天循环和桌面服务不退出；旧 `character.toml` 中带完整 API 字段的 `claude_agent` provider 也不会注册。跨进程 `models.json` 迁移/保存竞态未修改，理由见 PR。
- 合并后工作台回归：`executor_opencode` 100 项通过（2 项按环境跳过）、`maintenance` 43 项通过、`workbench_switch` 2 项通过、`turn_stop` 7 项通过；覆盖维护任务启动、执行、停止、取消、状态写回、资源清理及工作台切换。另有 model_settings 35、chat 89、web 67、tools 73（1 跳过）、autonomy 40、background 19、glance_model 6、delegate 9、mcp_services 23、tool_groups 10、tool_aliases 11、long_tasks 23、free_reply 31、cache_order 8、capability_transactions 7 项通过；Python 编译和 `node --check desktop/chat.js` 通过。
- 未完成：真实付费 API、真实 OpenCode 交互授权/停止、Windows 桌面实际点击未验证。PR #316 等最新 CI、必需检查和分支保护满足后合并。代码合并、自动检查通过、Windows 体验验收分别记录，不相互代替。

## 下一步

推送合并提交和文档更新，等 PR #316 最新 CI 全绿，确认必需检查与分支保护后合并。合并后请 Lan 按下面步骤验收。

## Lan 的 Windows 体验验收

1. 重启 Peter，打开设置 → 模型：Claude Code 的 Sonnet、Haiku、Opus 和通道入口消失；普通 API 模型、自定义模型、接口、API Key、价格和费用上限还在。
2. 用现有 API 模型聊一句，并让它读一条记忆、存一条记忆；分别切换聊天、自主活动、记忆后台、瞄一眼、委派模型，重启后确认各自仍然保留。
3. 用非 CC API 模型跑一次只读委派任务；再打开 OpenCode 工作台读取、修改一个测试文件、执行一次测试并停止；确认任务栏和进程里没有 VEIL 启动的 Claude CLI。
