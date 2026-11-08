# 当前状态

更新：2026-11-08 UTC · Copilot。

## 当前目标

修复 VEIL 对模型缓存 usage 的漏读和漏统计，准确核算 Gemini 4A 连续请求费用，同时确认真实缓存命中与代理账单一致。

## 现状

- 工单 252 的 PR #308 已合入 main（`548cfc1`，14 项检查通过）；工单 254 的 PR #310 已合入 main（`0c76ade`，4 个 Windows/Linux × Python 3.11/3.12 checks 成功）。PR #310 修复启动黑窗与容器测试误审批；Windows 实际复验仍待 Lan 完成。
- 工单 253 在独立 worktree `E:\Peter-Agent\VEIL\worktrees\253-gemini-cache-accounting`、分支 `ticket/253-gemini-cache-accounting`，已 rebase 到最新 main，代码和文档已提交，待推送并创建 PR。
- 已修复已知缓存 usage 字段归一化、缺失/无效/冲突状态记录、VEIL 直连模型各类别缓存统计，以及记忆 embedding 响应提供实际 usage 时的费用结算。未改变模型请求、消息顺序、人设、记忆上下文或工具 schema；未新增依赖或调用真实付费模型。
- 自动回归通过：聊天/SDK 88、缓存链路 8、自主活动 40、记忆召回 13、委派 9、模型设置 20、Native Memory 63、Memory 32、工具循环 71、瞄一眼 8、账本增量读取 11、账本恢复 22 项；Gemini usageMetadata 的 OpenAI SDK 单测另通过 1 项。
- `test_web.py` 未取得最终汇总，状态未验证且不重跑。没有中转站真实 `usage` JSON 或逐请求账单，Gemini 4A 实际缓存命中是否改善、实际收费是否一致均未验证。Ombre 子进程模型继续按估算记账，不纳入缓存命中统计。

## 下一步

推送工单 253 分支并创建 PR；核对最新必需 CI 与 Cubic 审查，修复实质问题后按分支保护决定是否合并。不得把缓存统计和本地假数据测试表述为真实 Gemini 命中已修复。

## Lan 的 Windows 体验验收

1. 使用 Gemini 4A 连续和 Peter 聊天，对照 `/cost` 与代理单请求 usage/账单；未报告缓存时必须显示未报告，不能虚报命中。
2. 使用记忆、普通工具、自主活动和 `galatea_garden` 连续读帖回复，确认能力与权限正常，VEIL 直连请求均有缓存状态和费用记录，且没有新增请求。
3. 对照代理账单确认实际收费一致；若代理不提供缓存 usage，确认 VEIL 显示无法核实。Ombre 子进程估算与 Windows 实机验收分别记录，未验收不写成通过。
