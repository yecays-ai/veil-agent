# 工单 227：Claude Code 接 VEIL 工具的隔离实验

## 目标
验证能否把 Claude Code 作为 VEIL 的一个新运行通道：由它负责推理和主动用工具，工具、聊天历史、记忆仍是 VEIL 的。本工单只做隔离沙盒实验，不接入正式运行系统。

## 当前状态
- 代码在 `experiments/cc-bridge/`：桥接 MCP（生产版 `Chat.send_message` / `stay_silent`，沙盒数据目录和假模型）、沙盒运行脚本（只读真实人设、记忆、聊天，只写工作目录；花园工具只放行人工核对过的 10 个显式工具名，不按名字前缀推断，`list_notifications` 因为会标记通知已读而排除；每次调用有回合数和折算价参数，整次运行有总折算价上限，订阅额度本身没有硬限制）。
- 实验报告：`experiments/cc-bridge/REPORT.md`。真实花园读取、自主调用、多气泡三项已在 lan 电脑上跑通。
- 真隔离验证：`experiments/cc-jail/`（Docker 内部网络、域名白名单代理、监督进程熔断）。CC 自己从 GitHub 链接调查、安装并接入 Time Server 并成功调用；桥接只提供在容器里执行命令和转发 stdio MCP，不含接入逻辑。
- 不改动正式运行系统：没有改 `veil_agent.py` 等任何正式代码。

## 验收
1. 读 `experiments/cc-bridge/REPORT.md`，核对「验收对照」五条的运行记录。
2. 决定是否进入下一步：把 Claude Code 作为聊天界面模型下拉里的一个通道（先只接聊天，不接自主醒来）。
3. `python -m unittest tests.test_cc_bridge` 通过。
