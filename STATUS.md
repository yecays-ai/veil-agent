# 当前状态

更新：2026-11-08 UTC · agent（接手 PR #312）。

## 当前目标

修复 VEIL 对模型缓存 usage 的漏读和漏统计，准确核算 Gemini 4A 连续请求费用，同时确认真实缓存命中与代理账单一致。

## 现状

- 工单 253 在分支 `ticket/253-gemini-cache-accounting` 上开发，PR #312 尚未合并；代码合并、自动检查通过、Windows 体验验收分别记录。
- 已完成：已知缓存 usage 字段归一化（OpenAI/LiteLLM、Gemini `usageMetadata` 与 SDK snake_case 对象）、缺失/无效/冲突状态记录、VEIL 直连模型各类别缓存统计，以及记忆 embedding 响应提供实际 usage 时的费用结算。
- Cubic 复审指出的问题已修复：embedding 结算不得超过预留（否则 `released_amount` 为负、绕过每日/每月上限）；非空 embedding 请求报告 `prompt_tokens=0` 时保留估算、不按零费用结算；补 Gemini SDK usage 对象与零用量的回归测试。
- 未改变模型请求、消息顺序、人设、记忆上下文或工具 schema；未新增依赖或调用真实付费模型。
- 本机自动回归：缓存、预算、召回、记忆、账本与语音相关 206 项通过（1 项跳过）；`test_web.py` 未取得最终汇总，未重跑。必需 CI 以 PR #312 的 GitHub 检查为准。
- 未取得中转站真实 usage JSON 或逐请求账单；Gemini 4A 实际缓存命中是否改善、实际收费是否一致均未验证。Ombre 子进程模型继续按估算记账，不纳入缓存命中统计。

## 下一步

查看 PR #312 最新必需 CI 与 Cubic 复审；只处理功能、计费正确性、数据或安全方面的实质问题，满足当前分支保护后合并。

## Lan 的 Windows 体验验收

1. 使用 Gemini 4A 连续和 Peter 聊天，对照 `/cost` 与代理单请求 usage/账单；未报告缓存时必须显示未报告，不能虚报命中。
2. 使用记忆、普通工具、自主活动和 `galatea_garden` 连续读帖回复，确认能力与权限正常，VEIL 直连请求均有缓存状态和费用记录，且没有新增请求。
3. 对照代理账单确认实际收费一致；若代理不提供缓存 usage，确认 VEIL 显示无法核实。Ombre 子进程估算与 Windows 实机验收分别记录，未验收不写成通过。
