# 交接

更新：2026-10-20 · Codex · 工单 023 PR #50 已开，等待 CI 与 CodeRabbit

## 做到哪

- 当前分支：`ticket/023-autonomy-chat-context`，PR [#50](https://github.com/yecays-ai/veil-agent/pull/50)，基于最新 main `6b44de6`，head `d8f08d6`。
- 用户确认两个缺陷：自主活动摘要未进入后续普通聊天；`Budget.display` 将自主费用只统计 `peter`，且自主完成原因也写死 Peter。
- 用户已同意将最近一次自主活动摘要带入每次后续聊天；摘要约 500 tokens 上限，按每日 20 次、输入 ¥2/M、未命中缓存估算约 ¥0.02/日。
- 修改方向：`Chat.send` 从当前角色独立的自主日志读取最近摘要作为现有 `extra_context`；损坏摘要安全停止；`Budget.category_totals(None)` 汇总全角色自主费用；显示原因使用角色配置名称。测试覆盖 Peter 后续聊天上下文和 Peter/Ghost 费用累计。
- GitHub 复用调查未发现适合该需求的外部成熟实现；复用 VEIL 已有上下文注入和角色日志，不加依赖。
- 定向测试 3 项、`py_compile` 与 `git diff --check` 已通过；PR CI 运行中，CodeRabbit 已请求评审，尚无评审结果。
- 工单 022 的独立 PR #49 仍开着：CI 已通过，CodeRabbit 评审处理中；Windows 启动验收未做。直接 Git push 遇连接重置，PR 远端分支通过 GitHub API 发布。该任务后续仍需完成评审与验收交接。

## 下一步

1. 查看 PR #50 CI 与 CodeRabbit 评审；只修影响功能或安全的问题。
2. 评审满足仓库条件后按规则处理合并，再请 lan 做 STATUS.md 中 Peter、Ghost 的 Windows 体验验收；自动化通过不代表体验已验收。
3. 继续留意 PR #49 的 CodeRabbit 评审；CI 绿且无需修的功能评论后，按规则处理合并，并要求退出旧服务再验收 Node/npx 恢复。

## 注意

- 每个 PR 必须更新 `STATUS.md` 和 `HANDOFF.md`。
- 不改模型或预算上限、不读取或改写真实记忆、聊天、人设和账本；不新增模型调用。
