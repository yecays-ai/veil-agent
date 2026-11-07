## 当前工单 249（2026-11-07 UTC）

- 目标：修复搜索文本里的 `git push` 等词误触发工作台审批，同时保持真正危险和复杂命令受控。
- 分支 `ticket/249-opencode-approval-classification`，独立 worktree `E:\Peter-Agent\VEIL\veil-agent-codex-249`。代码提交 `be24ecc` 及审批参数保护补充提交 `4fee53b` 均已推送；最新定向测试 45 项通过、2 项因未安装 OpenCode 跳过。正式运行目录未改动，原 `veil-agent-codex` 未提交改动原样保留。
- 已确认旧实现会对 `grep -R "git push" .` 弹审批。新定向测试 45 项通过、2 项因 OpenCode 未安装而跳过；语法和 diff 检查通过。README 全套 1218 项中 2 项备份断言失败、1 项缺少 `docx` 模块报错、13 项跳过；失败项不在本工单改动路径。
- 工单 248 / PR #300 已合并，合并提交 `b9695e52b34490ddbf51763ad9970f4b24874853`，Windows/Linux Python 3.11/3.12、审计和 scanner smoke 均通过；Windows Peter 实机体验验收未确认。
- PR #303 已存在并指向上述分支；标题和模板正文已补全，含影响范围及本地检查证据。必需 CI 的部分检查仍运行，Cubic 检查仍运行，尚未合并。

## 下一个窗口第一步

查看 PR #303 最新必需 CI 和 Cubic 审查；处理功能、安全、数据完整性或重大用户影响问题后，按实际分支保护规则合并。合并前确认 PR head 与分支最后提交一致。

## 背景和边界

- OpenCode v1.19.5 的 shell permission 请求 `patterns` 是逐条命令片段，`metadata.command` 是原始命令；复用现有请求机制，不新增权限系统。依据见 `docs/tickets/249-opencode-approval-classification.md`。
- 聊天与自主活动共用 `executor_opencode.run` 权限回调；费用预算、Docker 隔离、密钥保护、停止和快照实现未改，相关回归测试仍在定向测试中。
- 未安装 OpenCode，真实 OpenCode API 集成测试和 Windows Peter 体验验收未完成；不调用付费模型，不操作真实项目。
- PR 合并后只清理本工单创建且已确认无未提交修改的 worktree；不能清理原 `veil-agent-codex` 或正式目录。
