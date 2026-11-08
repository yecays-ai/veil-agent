# 工单 317 · 撤除 Claude Code 专用接入

## 目标

Lan 在 Peter 的模型设置里不再看到 Claude Code 专用 Sonnet、Haiku、Opus 或通道入口；API 聊天、工具、记忆、OpenCode 工作台、自主活动、记忆后台、瞄一眼和委派研究都照常工作。旧配置里选过 CC 模型的用途会恢复为可用 API 模型，不导致 Peter 启动失败。

## 当前状态

- 删除纯 CC 文件：`claude_agent_channel.py`、`claude_agent_bridge.py`、`scripts/claude_agent_smoke.py`、对应测试，以及 `experiments/cc-bridge/`、`experiments/cc-jail/`。
- `veil_agent.py` 删除 CC 导入、`toml_provider()` 的 CC 特殊配置、聊天执行器、成本日志写入、空回复、工作台、后台模型和委派成本里的 CC 分支。
- `model_settings.py` 删除 CC 用途限制和密钥状态；`models.json` 启动时只迁移已确认的 CC 内置 provider 引用，先写 `models.json.cc-removal-backup`，再原子替换，其他用途、自定义模型、接口、Key 和费用上限保留。
- `characters/peter/character.toml` 删除三个 CC provider，保留普通 Anthropic API 和其他 provider。
- 桌面端删除 CC 筛选和提示，模型卡片显示一次迁移提示。
- 本单依赖 316（PR #315）。316 合并后解决 `STATUS.md`、`HANDOFF.md` 和 `veil_agent.py` 的冲突，再审查合并本单。
- 未验证：真实付费 API、真实 OpenCode 授权/停止体验、Windows 桌面实际点击；这些留给 Lan 验收。

## 验收步骤

1. 重启 Peter，打开设置 → 模型：Claude Code 的 Sonnet、Haiku、Opus 和通道入口消失；普通 API 模型、自定义模型、接口、API Key、价格和费用上限还在。
2. 用现有 API 模型聊一句，让它读一条记忆并存一条；分别切换聊天、自主活动、记忆后台、瞄一眼、委派模型，重启后确认仍然保留。
3. 用非 CC API 模型跑一次只读委派任务；再打开 OpenCode 工作台读取、修改一个测试文件、执行一次测试并停止；确认任务栏和进程里没有 VEIL 启动的 Claude CLI。
