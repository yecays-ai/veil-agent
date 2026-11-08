# HANDOFF

## 当前工单 314（2026-11-08 UTC）

- 分支 `ticket/314-autonomous-maintenance` 从 `main` 的 `64648cb` 建立；PR #314：https://github.com/yecays-ai/veil-agent/pull/314。开发在独立 worktree 中；正式目录保持 `main`，未开发、未改源码。
- 工单 313 的任务范围授权已并入本分支并收紧：项目测试首次运行需一次任务级授权；安装按具体包授权；拒绝在同一任务内复用；Windows junction/reparse point、隐藏目录、通用词和 VEIL 自身数据目录不能成为工作台范围。
- 新增 `maintenance.py` 的维护任务状态、独立 worktree、主机侧 git/gh 交付、CI/审查读取、失败日志、独立测试实例、验收记录、合并门禁和恢复。
- `workbench` 支持 `task_id`；维护任务拒绝 git 写命令；OpenCode 容器只挂载 worktree 文件并屏蔽 `.git`；GitHub 长期凭据不进入容器。
- `web_agent.py` 支持 `--acceptance`/`--config-root`/`--data-dir`；桌面前端支持测试实例使用独立 userData 和后端数据目录；测试实例不启动自主活动、外部频道、记忆或桌面维护。
- 已验证：executor 67 项（2 跳过）、maintenance 9 项、chat/web/tools 228 项（1 跳过）、支持模块 55 项、Hub/停止 21 项、桌面 Node 后端 4 项通过；编译和 JS 语法检查通过。Windows 实际验收、PR CI 和 Cubic 尚未开始。

## 下一个窗口第一步

提交并推送分支，创建 PR；读取 PR 的实际必需 CI 和 Cubic 审查，只修功能、数据、安全或交付阻断问题。满足分支保护后合并，然后请 Lan 按 `STATUS.md` 的验收步骤体验。

## 已知风险与边界

- 测试实例依赖正式目录的 Python 环境、`characters`/`persona` 和 `.env`；启动时复制到隔离目录，就绪后删除测试目录中的 `.env`。如果启动失败，保留证据并报告，不写正式数据。
- `maintenance.py` 的远程事实来自 `gh` 和 GitHub API；本机没有 `gh` 认证时应明确报告，不能假装 PR/CI 已存在。
- 未在 Windows 上实际跑通完整“测试实例桌面窗口”路径；当前验证覆盖后端启动、隔离数据目录和桌面连接代码，Windows 实际体验待 Lan 验收。
- 不删除正式目录或其他正在使用的 worktree；合并后仅在本次 worktree 无未提交改动时清理。
