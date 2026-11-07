## 当前工单 249（2026-11-07 UTC）

- 目标：修复搜索文本里的 `git push` 等词误触发工作台审批，同时保持真正危险和复杂命令受控。
- 分支/worktree `ticket/249-opencode-approval-classification`。实现与文档状态已推送。正式运行目录未改动，原开发 worktree 的未提交改动原样保留。
- 已确认旧实现会对 `grep -R "git push" .` 弹审批。Cubic review 指出的 `git.exe`、`rd /s` 别名、shell 命令替换、source 与动态解释器执行，以及 HANDOFF 中的绝对本机路径均已本地修复；新增回归后定向测试 46 项通过、2 项因 OpenCode 未安装而跳过。修复尚未推送。
- 前一轮定向测试 45 项通过、2 项因 OpenCode 未安装而跳过；README 全套 1218 项中 2 项备份断言失败、1 项缺少 `docx` 模块报错、13 项跳过；这些全套失败项与本工单改动无交集。
- 工单 248 / PR #300 已合并，合并提交 `b9695e52b34490ddbf51763ad9970f4b24874853`，Windows/Linux Python 3.11/3.12、审计和 scanner smoke 均通过；Windows Peter 实机体验验收未确认。
- PR #303 已存在并指向上述分支；标题和模板正文已补全，含影响范围及本地检查证据。当前提交 241358d 的 Windows/Linux CI 有检查正在运行；修复推送后需等待最新 head 的 Cubic 复审和必需 CI。

## 下一个窗口第一步

下一步更新 PR 正文、回复并解决 Cubic 实质 review 线程，确认 docs 不含本机绝对路径，然后推送修复。等待最新 head 的 Cubic 与 CI。main ruleset `VEIL Main Protection` 要求六个状态检查通过（Windows/Linux Python 3.11/3.12、audit、scanner-report-smoke），PR review 批准数为 0。合并前确认最新 head 通过这些检查并满足 ruleset。

## 背景和边界

- OpenCode v1.19.5 的 shell permission 请求 `patterns` 是逐条命令片段，`metadata.command` 是原始命令；复用现有请求机制，不新增权限系统。依据见 `docs/tickets/249-opencode-approval-classification.md`。
- 聊天与自主活动共用 `executor_opencode.run` 权限回调；费用预算、Docker 隔离、密钥保护、停止和快照实现未改，相关回归测试仍在定向测试中。
- 未安装 OpenCode，真实 OpenCode API 集成测试和 Windows Peter 体验验收未完成；不调用付费模型，不操作真实项目。
- PR 合并后只清理本工单创建且已确认无未提交修改的 worktree；不能清理原 `veil-agent-codex` 或正式目录。
