# HANDOFF

## 工单 316：自动与真实 OpenCode 验证完成，等待 Lan 验收（2026-11-09 02:55 +08）

- 工作分支 `ticket/316-maintenance-execution-contract`，PR #315，基于 `b7847ca`。GitHub 编号是 315，对应工单 316；不是先前停止的工单 315（复制消息时间）。
- 工单 315 的 worktree、已有修改和运行状态保持原样；没有恢复、删除或重新测试它。正式运行目录 `E:\Peter-Agent\VEIL\veil-agent` 保持 `main`，未在其中开发。
- 本轮在既有交接上继续，没有重做或覆盖之前的中间成果。

## 本轮完成

- 安全复核：容器只挂载项目；宿主 `.git` 元数据隐藏；GitHub 凭据不进入容器；敏感文件/目录遮蔽；新增拒绝项目内指向项目外的文件链接（symlink / Windows reparse point），防止通过挂载读取宿主文件。
- 状态机复核：任务级执行锁；停止与交付/检查/验收/合并的并发处理；审批等待后再次检查停止；过期状态不能复活已停止任务；已合并任务不能被过期停止覆盖；停止期间启动的验收实例会被停止并清理；PR 查询失败不再当成“没有 PR”；无变更重复交付不新建提交。
- 交付与恢复：真实 OpenCode + 假模型 + 假 GitHub 的成功交付，以及“测试失败→阻止交付→修复重测→交付→检查→验收→合并”的失败恢复；Git 快照只包含本次运行后的改动，敏感目录不进入快照；宿主崩溃时容器看门狗自动退出。
- 验收快照：从 PR 提交的 Git 对象逐文件校验并生成独立代码快照；工作区改动、`git archive` 的 Windows 行尾替换和导出替换都不会改变验收版本。

## 验证证据

- `python -m unittest tests.test_maintenance tests.test_executor_opencode tests.test_chat tests.test_web tests.test_tools -q`：358 项通过，3 项按环境跳过，退出码 0（308.6s）。
- `python -m unittest tests.test_maintenance tests.test_executor_opencode -q`：130 项通过，2 项跳过（宿主机未安装 OpenCode；真实 OpenCode 在 `veil-workbench` 容器内运行）。
- 单独复跑真实链路：`test_real_opencode_to_fake_github_delivery`、`test_real_opencode_failure_then_fix_delivers_to_fake_github`、`test_real_acceptance_backend_starts_from_snapshot_with_fake_config` 均通过（3 项，59.5s）。
- 真实 Windows 启动检查：从当前提交快照启动验收后端，并启动真实 Electron 外壳；2 个进程存活、窗口出现；停止后无残留测试进程、容器或临时目录。未做桌面点击/聊天体验验收。
- 未调用真实付费模型；未使用真实 GitHub 写入；依赖 Docker 的测试在无 Docker 环境会跳过。

## 仍未完成

- Lan 的真实 Windows 桌面体验验收（`STATUS.md` 的三步）。
- 真实付费模型的自主决策质量、真实 GitHub 账号/权限/分支保护下的交付。
- 容器测试依赖本机 Docker；CI 的 Ubuntu/Windows 作业没有本机容器，因此这部分只能在有 Docker 的机器上跑。
- 容器看门狗在宿主长时间卡顿（约 10 秒以上无法响应健康检查）时可能误停工作台；需要真实体验确认是否影响正常使用。

## 下一个窗口第一步

1. 保持 PR #315 为草稿，先请 Lan 按 `STATUS.md` 的三步验收；不要在她验收前合并，也不要让她反复点审批卡来定位问题。
2. 记录她的实际体验结果。通过后请求 Cubic 审查，确认最新提交的必需 CI 和 GitHub 实际分支保护，再按仓库流程合并。
3. 合并后按仓库规则只清理本次任务创建且确认无未提交修改的 worktree；工单 315 的 worktree 不在清理范围。
