# 交接

更新：2026-10-20 UTC · 014 已合并；工单 015 删除语义

## 做到哪

- 最新 `main` 基线 `b1d8555`；当前分支 `ticket/014-chat-stability`；PR #31 指向 `main`。
- 012：代码已合并，真实缓存效果待验收。013：代码已合并，真实 Windows 网页已验收。014：当前稳定性修复开发中。权威进度和验收步骤见 `STATUS.md`。
- CodeRabbit 已发现的问题均已修复：失败后刷新当前聊天历史；模型失败时清理终端输入缓冲；同一 request id 不同正文返回冲突；网页成功后清理旧草稿 id，同时保留用户在请求期间新输入的内容；去重表限定为最近 1024 条并只存正文摘要；普通与工具模型调用中的 `UserError` 原样上抛，账务完整性问题不会被误判成可恢复 provider 故障。
- 本机全量假数据测试通过：141 项，3 项可选集成跳过；网页内联脚本 `node --check` 与 `git diff --check` 通过。网页服务测试需本机 loopback 权限。未调用真实模型、供应商接口或付费 API。
- 上一个 head 的 CI #195 通过；最新改动后的 PR #31 CodeRabbit 复审与 CI 尚待完成。真实 Windows 验收待 lan 完成。不得把自动测试当作用户体验验收。
- 用户原有未提交改动 `characters/peter/character.toml`（thinking 从 disabled 改为 enabled）仍在工作区，不能纳入工单提交。未编辑真实记忆、聊天数据或费用账本。
- 直接 GitHub 推送曾超时；前次文档更新经 GitHub API 写入远端。此后本地分支与远端提交历史可能不同步，下一步提交前先查 PR 分支最新 SHA；不要对旧远端历史强推。

## 工单 015（删除语义）

`memory_agent.py` 的 `/restore` 对已删除记忆只回说明；`SOFT_DELETE_NOTE`、`DELETED_NOT_RESTORABLE` 是提示文案。依据见 `docs/tickets/015-delete-semantics.md`。不要再给“删除”加恢复路径；要恢复请用 `/forget`。

## 下一个窗口第一步

先查看 PR #31 最新 head、CI 和 CodeRabbit 复审状态；对照本地工作区，确认新增修复与 `STATUS.md`、本文件都在同一 PR 内容中。只处理会影响功能或安全的问题。CI 和评审满足规则后合并，再请 lan 按 `STATUS.md` 做真实 Windows 验收；完成前不要宣布工单完全验收。

## 之后的工单

实验室（4A）工单已写好：`docs/tickets/016-lab.md`，草稿补丁 `/mnt/project-files/lab-ticket/016-lab-draft.patch`（未评审，仅供参考）；计划见 STATUS.md 的链接。lan 让 Codex 施工；开工前读工单、STATUS、新计划。

## 背景和注意

- 聊天共用 `veil_agent.run_chat`；网页只接入 Hub 输入、输出、审批和 HTTP 服务。终端流程必须保持原样。
- 费用请求在发起模型请求前已预留并写入用户消息。结果不确定时保留预留，不自动重试；账本、工具或本地持久化异常继续安全停止。
- 网页 `/api/send` 使用 `request_id` 去重；同 id 同正文视为重复，同 id 不同正文冲突，最多保留最近 1024 条 ID 摘要。`Hub.busy` 检查和入队持同一把条件锁。审批仅在当前 pending id 有效时接受，页面等服务器响应后报告结果。
- `reuse-first` 结论：沿用 Python 标准库 HTTP 服务及既有聊天、审批、预算组件，不引入整套聊天框架或新依赖。
- 012 真实缓存效果仍待验收；011 跨重启授权与路径边界仍待真实 Windows 验收。
