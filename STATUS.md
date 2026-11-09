# 当前状态

更新：2026-11-09 08:35 (+08) · 工单 317。

## 当前目标

完成 Claude Code 专用接入撤除并合入 `main`：模型设置不再出现 CC 模型和通道，正式运行不再查找、启动或管理 Claude CLI，同时保留 API 聊天、工具、记忆、OpenCode 工作台和五类独立选模。

## 现状

- 工单 317 在 `ticket/317-remove-claude-code`，PR #316：https://github.com/yecays-ai/veil-agent/pull/316。分支已同步 `main` 的 `b70ddaa`；PR 等待最新 CI 和实际分支保护满足后合并。
- 已删除 CC 独立执行器、MCP 桥、Claude CLI 发现、后台子进程、专属实验、冒烟脚本和专属测试；`veil_agent.py`、`model_settings.py`、`characters/peter/character.toml` 与桌面端已完成定向删除。OpenCode、维护任务、MCP、委派、费用和工具等共享基础设施保留。
- 旧 `data/<角色>/models.json` 中已确认的 CC 内置模型引用会迁移：先保留 `models.json.cc-removal-backup`，再原子替换。聊天和记忆后台优先恢复已填写 Key 的 API 模型；没有 Key 时明确提示；没有普通 API 模型时以显式“未选择模型”启动，设置页仍可保存首个接口和模型。自主活动和瞄一眼恢复默认选择，委派清空并提示重选；其他用途、自定义模型、接口、API Key、价格和费用上限不变。
- 缺少 `providers` 表或默认 provider 被删除/是 CC 时可恢复启动；普通默认 provider 本身配置无效时仍报明确配置错误，不会静默换到另一个付费模型。缺模型/Key 按可恢复请求错误处理，聊天循环和桌面服务不退出；此时模型相关的 Ombre 记忆后台暂不启动，并提示选择模型后重启。
- `models.json` 的迁移、保存和模型切换通过同一跨进程文件锁串行；保存/切换前检查文件是否已被另一个进程修改，发现旧快照会拒绝覆盖并提示刷新。锁文件是 `data/<角色>/models.json.lock`，不写入用户配置。
- 合并后工作台回归：`executor_opencode` 100 项通过（2 项按环境跳过）、`maintenance` 43 项通过、`workbench_switch` 2 项通过、`turn_stop` 7 项通过；覆盖维护任务启动、执行、停止、取消、状态写回、资源清理及工作台切换。另有 model_settings 38、chat 90、web 67、tools 73（1 跳过）、autonomy 40、background 19、glance_model 7、delegate 9、mcp_services 23、tool_groups 10、tool_aliases 11、long_tasks 23、free_reply 31、cache_order 8、capability_transactions 7 项通过；Python 编译和 `node --check desktop/chat.js` 通过。
- 未完成：真实付费 API、真实 OpenCode 交互授权/停止、Windows 桌面实际点击未验证。代码合并、自动检查通过、Windows 体验验收分别记录，不相互代替。

## 下一步

等 PR #316 最新 CI 全绿，确认必需检查与实际分支保护后合并。合并后请 Lan 按下面步骤验收。

## Lan 的 Windows 体验验收

1. 重启 Peter，打开设置 → 模型：Claude Code 的 Sonnet、Haiku、Opus 和通道入口消失；普通 API 模型、自定义模型、接口、API Key、价格和费用上限还在。
2. 用现有 API 模型聊一句，并让它读一条记忆、存一条记忆；分别切换聊天、自主活动、记忆后台、瞄一眼、委派模型，重启后确认各自仍然保留。
3. 用非 CC API 模型跑一次只读委派任务；再打开 OpenCode 工作台读取、修改一个测试文件、执行一次测试并停止；确认任务栏和进程里没有 VEIL 启动的 Claude CLI。
