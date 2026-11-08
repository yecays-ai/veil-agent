# 当前状态

更新：2026-11-08 UTC · agent（工单 314）。

## 当前目标

让 Peter 通过现有 OpenCode 工作台维护 VEIL 自身：独立 worktree 开发、测试修复、GitHub PR、CI/审查、独立测试实例验收、授权合并和正式更新通知；Lan 不需要打开其他编程 Agent 接替。

## 现状

- 工单 314 在分支 `ticket/314-autonomous-maintenance` 上开发，基于 `main` 的 `64648cb`。PR #314：https://github.com/yecays-ai/veil-agent/pull/314。正式运行目录保持 `main`，未在其中开发。
- 复用并收紧了工单 313 的任务范围授权；313 的实现作为本分支的起点，已修复其审查中的实质问题：测试执行需要一次任务级授权、安装按包授权、拒绝后不重复询问、Windows junction/隐藏目录/通用词不得选成范围、VEIL 自身数据目录不可作为项目。
- 新增 `maintenance.py`：
  - 任务状态持久化在角色数据目录，记录仓库、worktree、分支、提交、PR、CI、审查、验收、授权、阻碍和下一步。
  - 从正式仓库最新 `main` 建立独立 worktree；提交和推送由主机侧执行；PR 通过已认证的 `gh` 创建或更新，不重复创建。
  - 读取真实 PR 状态、必需 CI、失败日志和审查；只有“实际验收通过或 Lan 明确同意跳过 + 必需检查通过 + 审查无阻断 + 合并授权 + PR 状态核实”才合并。
  - 支持 `status`/`resume` 按真实 worktree、提交和 GitHub 状态恢复；`stop` 后拒绝继续推送或合并。
  - 测试实例使用开发 worktree 的代码、复制的角色配置和独立测试数据；不启动自主活动、外部频道、记忆或桌面维护，不写正式聊天、记忆和凭据。
- `workbench` 可带 `task_id` 在维护 worktree 中开发；维护任务中拒绝 git 写命令；容器只挂载 worktree 文件并屏蔽 `.git` 文件；长期 GitHub 凭据不进入 OpenCode 容器。
- 自动检查：`test_executor_opencode` 67 项通过（2 项按环境跳过）；`test_maintenance` 9 项通过；`test_chat`/`test_web`/`test_tools` 228 项通过（1 项跳过）；工具分组、别名、长期任务和更新入口 55 项通过；Hub/停止相关 21 项通过；桌面 Node 后端 4 项通过；Python 编译和 `node --check` 通过。
- 未完成：PR #314 的必需 CI、Cubic 审查和 Windows 实际验收均未开始。

## 下一步

提交并推送本分支，创建 PR；处理必需 CI 和 Cubic 的实质问题；满足分支保护后合并。合并后请 Lan 按下面步骤验收，并用一次真实的小功能需求验证完整自主维护流程。

## Lan 的 Windows 体验验收

1. 在 Peter 聊天窗口提出一项真实、范围较小的 VEIL 功能需求，并说“完成后提交 PR”；观察 Peter 自己找到仓库、建立独立 worktree、修改和测试、推送并创建 PR，不需要打开其他编程 Agent。
2. 打开 Peter 提供的独立测试实例实际使用新功能；确认正式 Peter 的聊天、记忆和数据没有被改动，未验收时不会被自动合并。
3. PR 合并后运行 `update-main.cmd` 并重启正式版本，确认新功能可用，原有聊天、记忆和 OpenCode 工作台正常。
