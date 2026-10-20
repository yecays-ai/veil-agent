# 交接

更新：2026-10-20 UTC · 工单 014 聊天稳定性与异常恢复施工中

## 做到哪

- 最新 `main` 已核实为 `b1d8555`；当前分支 `ticket/014-chat-stability`。尚未提交或创建 PR。
- 已确认 012 代码合并、真实缓存效果待验收；013 代码合并、真实 Windows 网页已验收。权威进度见 `STATUS.md`。
- 014 已改 `veil_agent.py`、`web_agent.py`、`web/index.html`，并新增针对普通模型错误恢复、请求去重和并发提交的测试；新增 `docs/tickets/014-chat-stability.md`。`STATUS.md` 已标记三项工单状态。
- 通过：`python -m unittest tests.test_chat tests.test_web`（43 项）；全量 `python -m unittest discover -s tests`（137 项，3 项可选集成跳过）。本机网页测试需 loopback 权限；未调用真实模型、供应商接口或付费 API。
- 尚未完成最终代码复核、PR、CodeRabbit/CI 或真实 Windows 用户验收。
- 保留了原有未提交的人设配置改动 `characters/peter/character.toml`（thinking 从 disabled 改为 enabled）；不要纳入工单提交。未编辑真实记忆、聊天数据或费用账本。

## 下一个窗口第一步

先读 `AGENTS.md`、`STATUS.md`、本文件和 `docs/tickets/014-chat-stability.md`，检查最终差异并确认 `characters/peter/character.toml` 仍保持为未提交的独立用户改动；若无新问题，提交并推送本分支、创建 PR，之后处理 CodeRabbit 和 CI。不要把自动测试通过等同于真实 Windows 验收。

## 背景和注意

- 聊天共用 `veil_agent.run_chat`；网页只接入 Hub 输入、输出、审批和 HTTP 服务。终端流程必须保持原样。
- 费用请求在发起模型请求前已预留并写入用户消息。结果不确定时必须保留预留，不自动重试。
- 网页 `/api/send` 现在要求 `request_id`；前端对同一草稿重试会复用该 ID，服务器已接收过的 ID 不可再入队。
- `Hub.busy` 的检查与入队必须持同一把条件锁；审批仅在当前 pending id 有效时接受，页面需等服务器响应后报告已收到。
- 工具、账本或本地持久化异常不能被归类为可恢复的模型网络故障。
- `reuse-first` 结论：沿用 Python 标准库 HTTP 服务和既有聊天/审批/预算组件；不接入 Open WebUI、LobeChat 等需要另行装配后端的整套产品，也不加依赖。
- 012 真实缓存收益仍未验收；011 跨重启授权与路径边界仍未完成真实 Windows 验收。不要把自动测试当成用户体验验收。
