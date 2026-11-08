# HANDOFF

## 当前工单 253（2026-11-08 UTC）

- PR #310 已合入 main（`0c76ade`），GitHub API 显示 4 个 Windows/Linux × Python 3.11/3.12 checks 成功；Windows 桌面启动和连续工作台任务仍待 Lan 实机复验。工单 252 的 PR #308 也已合并（`548cfc1`，14 项检查通过）。
- PR #312 已创建并推送，分支 `ticket/253-gemini-cache-accounting`，提交 `b1b29a5`；必需 CI 和 Cubic 审查待完成。worktree 为 `E:\Peter-Agent\VEIL\worktrees\253-gemini-cache-accounting`。
- `make_model()` 解析已知 OpenAI/LiteLLM 与 Gemini `usageMetadata.cachedContentTokenCount` 形式；缺失、无效、冲突状态不折扣，明确 0 与未报告不同。`/cost` 汇总聊天、自主、瞄一眼、短期便签和委派的直连模型请求。
- `Chat.send()` 上下文顺序和工具 schema 未改；普通聊天、工具循环、短期便签提取、Autonomy/Glance、委派和 Garden MCP 继续复用原模型入口。记忆向量召回由独立 `QueryEmbedder` 请求；Ombre 子进程 usage 不回传，仍按估算记账，不计为缓存命中。
- 记忆向量请求在响应有合法 `prompt_tokens` 时按实际 usage 结算；无报告/失败时走原估算，不新增请求。
- 自动回归通过：聊天/SDK 88、缓存链路 8、自主 40、记忆召回 13、委派 9、模型设置 20、Native Memory 63、Memory 32、工具循环 71、瞄一眼 8、账本增量读取 11、账本恢复 22 项；Gemini `usageMetadata` 的 SDK 单测另通过 1 项。
- `test_web.py` 未取得最终摘要，不重跑；中转站真实 `usage` 与对应费用行尚未取得，实际 Gemini 4A 命中改善和收费一致性均未验证。正式目录进程由 `web_agent.py` 启动，已保留未动。

## 下一个窗口第一步

查看 PR #312 最新 SHA、必需 CI 和 Cubic 审查；只处理功能、计费正确性、数据或安全方面的实质问题。满足当前分支保护后才能合并。`test_web.py` 没有最终结果，不要重跑；真实 Gemini 4A 实际命中仍待 Lan 按 `STATUS.md` 验收。

## 尚未解决

- 缺少 `api5.jiushi.cyou` 的脱敏真实响应 `usage` 和对应账单，不能判定 Gemini 4A 全额计费根因是代理字段映射、上游未命中还是代理计费。
- Fake transport 证明 VEIL 解析和本地费用逻辑；真实连续请求是否利用了稳定前缀、工具结构是否影响上游缓存、实际账单是否一致，需在 Windows 体验验收。
- `test_web.py` 没有最终结果，不重复启动；以当前证据标为未验证。
- 正式运行目录及其现有服务、人设、真实聊天和记忆未改动。
