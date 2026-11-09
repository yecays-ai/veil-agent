# 工单 329：宿主机执行通道（执行层改造阶段 1）

## 目标

Peter 的工作台直接在 Lan 的 Windows 上连续干活（查资料、写代码、操作软件），Lan 照常用电脑不会打断整次任务；所有真实操作只经过 VEIL 一个检查点。

## 做法（Lan 已选方向 A）

- OpenCode 固定 1.19.5，直接跑在宿主机；桌面用 Windows-MCP 0.8.8（uvx，Python 3.14），后台浏览器用 Playwright MCP 0.0.84（无窗口、内存里的新配置）。
- OpenCode 拿不到这两个 MCP 的原始接口，也拿不到 PowerShell 工具：只连 VEIL 的网关（`veil_mcp_gateway.py`），每次调用带真实参数交给 `veil_checkpoint.py` 裁决。
- 检查点按 4 类（电脑操作、文件与代码、网络与浏览器、系统操作）回答：直接做 / 问 Lan / 禁止；每次裁决和每个工具错误原文写进 `data/<角色>/workbench-home/host-runs/*.jsonl`。
- 步数、时间、费用、停止仍只在 `executor_opencode.run` 一处。
- 开关：`executor.host_mode`（character.toml 或本机 `data/<角色>/workbench.json`），默认关。VEIL 自维护仍走 Docker。旧的主聊天桌面、浏览器和容器工作台不变。

## 验收（开发侧先在 Lan 电脑用远程控制跑通，再请 Lan 体验）

1. “读一个公开网页，把摘要写进记事本，再切回来”：一次完成；过程中 Lan 照常打字、动鼠标，任务不被整体叫停。
2. “研究一下 XX 软件能干什么，试着用一下”：连续 20 步以上。
3. “这个项目跑不起来，你查一下”：自己找项目、看日志、改代码、跑测试。
4. 让他读 `.env`、删 Lan 的文件、装软件：分别被拒绝、拒绝、弹卡问。
5. 任务中按停止：马上停，`host-runs` 日志写清停在哪一步。
