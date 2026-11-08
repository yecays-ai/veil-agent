# 工单 314：Peter 自主维护 VEIL，并交付到 GitHub

## 目标

Lan 只在 Peter 的聊天窗口提出一项真实的 VEIL 开发需求，Peter 就能在独立 worktree 中开发、测试和修复，推送分支、创建 PR、跟踪 CI 与审查、提供独立测试实例，并在验收和合并授权满足后合并；Lan 仍用现有 `update-main.cmd` 更新正式版本。

## 当前状态

- 复用并收紧了工单 313 的任务范围授权：`TaskScope` 只在本次任务内复用批准和拒绝；项目测试首次运行需要一次明确的任务级授权；安装按具体包授权；Windows junction/reparse point、隐藏目录、通用词和 VEIL 自身数据目录不再能被选成工作台范围。
- 新增 `maintenance.py`：持久化任务状态；从正式仓库的最新 `main` 建立独立 worktree；通过主机侧 `git` 和已认证的 `gh` 提交、推送、创建/更新 PR；读取真实 CI、审查和失败日志；启动独立测试实例；按“验收 + 必需检查 + 审查 + 合并授权 + PR 状态”合并；支持停止、恢复和不重复执行。
- `workbench` 可带 `task_id` 在维护 worktree 中开发；维护任务中拒绝所有 git 写命令，OpenCode 容器只挂载 worktree 文件，`.git` 文件被屏蔽，长期 GitHub 凭据不进入容器。
- `web_agent.py` 支持验收实例：开发 worktree 的代码、复制的角色配置和独立测试数据；测试实例不启动自主活动、外部频道、记忆或桌面维护，不写正式聊天、记忆和凭据。
- 复用调查：`gh` CLI（官方，MIT，46.9k stars）负责远程 PR 和检查；Git 原生 worktree 负责隔离；`kdcokenny/opencode-worktree`（722 stars）和 `coderabbitai/git-worktree-runner`（1809 stars）只借“worktree 放在仓库外、自动清理”的思路，前者已停止维护且仅支持 OpenCode V1，后者是 Bash，均不引入依赖。OpenCode 权限仍由 VEIL 的审批桥裁决，不依赖项目内配置。
- 自动检查：`test_executor_opencode` 67 项通过（2 项按环境跳过）；`test_maintenance` 9 项通过；`test_chat`、`test_web`、`test_tools` 等 228 项通过（1 项跳过）；工具分组/别名/长期任务/更新入口 55 项通过；Hub 与停止相关 21 项通过；桌面 Node 后端测试 4 项通过；Python 编译和 `node --check` 通过。
- 代码合并、自动检查、Windows 实际验收分别记录；本工单尚未开 PR，Windows 验收未开始。

## 验收步骤

1. 在 Peter 聊天窗口提出一项小而真实的 VEIL 功能需求，并说“完成后提交 PR”；确认 Peter 自己找到仓库、建独立 worktree、用 OpenCode 修改和测试、推送并创建 PR，不需要 Lan 打开其他编程 Agent。
2. 在 Peter 给出的独立测试实例中实际使用新功能，确认正式 Peter 的聊天、记忆和数据没有被改动；未验收时 PR 不应自动合并。
3. 合并后按 Peter 的提示运行 `update-main.cmd`、重启正式版本，确认新功能可用，原有聊天、记忆和 OpenCode 工作台正常。
