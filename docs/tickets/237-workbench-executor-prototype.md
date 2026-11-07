# 工单 237：工作台（OpenCode 执行后端）隔离原型

## 目标

Peter 用普通 API 模型也能自己搜索、改文件、跑命令、解决问题，聊天和自主醒来两条路径都能用；人格、记忆、审批、停止、费用和任务连续性始终归 VEIL；OpenCode 不可用时，Peter 原有的聊天、记忆和自主活动完全不受影响。

## 当前状态（原型，不合并，不进正式运行目录）

- 新增 `executor_opencode.py`：一个工具 `workbench`。Peter 的聊天/自主模型决定何时把活交给它；它临时启动 `opencode serve`（用 Peter 当前模型和中转），只在 `data/<角色>/workbench` 文件夹里干活，结束后杀掉整个进程树。
- 权限由 VEIL 程序决定，不靠 OpenCode 默认值：工作台文件夹内的命令、改文件、联网读页面直接做；危险命令、`.env`/secrets、推送代码一律拒绝；文件夹之外，聊天里弹同意卡问 lan，自主醒来里直接拒绝；OpenCode 里其他 MCP 工具一律不批准。
- 硬上限在 VEIL 一侧：每次 30 步、10 分钟、同样调用最多重复 3 次、每次费用上限（聊天 ¥0.5，自主醒来 ¥0.3，可在 `[executor]` 里改）；先在共享账本预留，结束按实际折算结算（算入当天总额，自主醒来算入自主额度）；lan 按停止立即中止。
- `character.toml` 里 `[executor] enabled = true` 才开，默认关；找不到 OpenCode 时工具不出现。
- 云端用脚本化假模型验证：12 项单元测试 + 1 项真实 OpenCode 集成测试通过。真实中转和 Windows 的测试在 lan 电脑 `oc-test` 文件夹里（`run-executor.cmd`）。

## 验收步骤（Lan 在 Windows 上做）

1. 双击 `E:\Peter-Agent\VEIL\oc-test\run-executor.cmd`，等报告 `report-exec-*.md`：聊天路径修 bug、自主路径新建并运行脚本、越界在聊天里被询问/在醒来里被拒、中途停止、费用上限、OpenCode 缺失和密钥错误时的恢复，都应为「通过」，密钥不出现在报告里。
2. 全量接入（让 Peter 在聊天里修一个测试项目，再在自主醒来时继续另一项未完成任务）需要把本分支放进开发 worktree 运行，等 lan 同意后再做。
