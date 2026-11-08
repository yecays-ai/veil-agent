# HANDOFF

## 工单 316：授权与网络边界已补齐，等待 Lan 验收（2026-11-09 04:15 +08）

- 工作分支 `ticket/316-maintenance-execution-contract`，PR #315，基于 `b7847ca`。这是工单 316，不是先前停止的工单 315（复制消息时间）。
- 工单 315 的 worktree、已有修改和运行状态保持原样；没有恢复、删除或重新测试。正式运行目录 `E:\Peter-Agent\VEIL\veil-agent` 保持 `main`。
- 本轮在既有交接上继续，没有重做或覆盖之前的中间成果。

## 本轮补齐的授权与安全边界

- 容器工作区里的普通测试不再要求审批卡：测试在 `veil-workbench` 容器内由无特权 `node` 用户执行，出口防火墙只允许模型中继 `veil-relay` 和本次任务已批准的网络主机；测试代码无法直接访问外网，也无法使用 Docker DNS 做数据外传。
- 权限判断改为默认拒绝：只有明确列出的本地读/写/测试命令自动放行；`ssh`、`nc`、`rm`、`git branch -d`、`git diff --output=...`、`git log --output=...` 等未知或写操作会询问 Lan，不再自动允许。
- 批准的安装/联网命令通过同一中继访问本次命令涉及的 URL 主机和包管理器主机（pip、npm、apt、cargo、go）。测试不能新增主机；`ssh`/`nc` 等非 HTTP 直连即使批准也会被防火墙挡住。
- 容器防火墙同时拒绝 IPv6 出口和 Docker DNS；工作区只保留项目挂载、模型中继和已批准主机。
- 镜像 `veil-workbench` 已更新：安装 `iptables`/`iproute2`，带 `veil.workbench.network-lock=1` 标签。`workbench-switch.cmd` 会重建旧镜像；`executor_opencode.run` 遇到旧镜像会明确拒绝，不会无隔离运行。
- 这些网络锁对**所有容器工作台任务**生效，不只维护任务；非容器工作台仍保留测试审批（宿主没有网络隔离）。

## 验证证据

- 专项：`python -m unittest tests.test_maintenance tests.test_executor_opencode -q` —— **136 项通过，2 项按环境跳过**（215.9s）。
- 全量相关回归：`python -m unittest tests.test_maintenance tests.test_executor_opencode tests.test_chat tests.test_web tests.test_tools -q` —— **364 项通过，3 项按环境跳过**（309.6s，退出码 0）。
- 最终提交上复跑代理/网络锁定向测试：`KeyProxyTests`、`MaintenanceContainerTests`、真实容器“测试自动放行且直接联网被阻断”共 7 项通过。
- 真实 OpenCode 容器：测试命令自动放行、直接联网被阻断、失败修复重测、拒绝、审批中停止、Git 快照、敏感目录遮蔽、宿主崩溃看门狗均通过。
- 真实 Windows：从提交快照启动验收后端和真实 Electron 外壳，2 个进程存活、窗口出现，停止后无残留测试进程、容器或临时目录。未做桌面点击/聊天体验验收。
- 未调用真实付费模型；未使用真实 GitHub 写入；依赖 Docker 的测试在无 Docker 环境跳过。

## 已知限制与风险

- 只有 HTTP/HTTPS 类命令能通过中继；`ssh`/`nc` 等非 HTTP 协议即使 Lan 批准也会被防火墙拒绝。这是有意的安全限制。
- 已批准的安装/联网会打开相应包管理器主机；测试可读取环境里的中继令牌并访问这些已批准主机，仍属于残余风险（不能访问任意主机）。
- 旧镜像必须重建；`workbench-switch.cmd` 会按标签自动重建，直接运行旧镜像会被拒绝。
- 网络锁在 Windows Docker Desktop 上已实测；其他 Docker 发行版/内核未逐一验证。
- 非容器工作台仍会在首次测试时询问一次；它没有容器网络隔离。
- 容器看门狗在宿主长时间卡顿（约 10 秒以上无法响应健康检查）时可能误停工作台。
- Lan 的真实 Windows 桌面操作体验、真实付费模型自主决策、真实 GitHub 账号/权限/分支保护仍未验收。

## 下一个窗口第一步

1. 保持 PR #315 为草稿，先请 Lan 按 `STATUS.md` 的三步验收；不要在她验收前合并，也不要让她反复点审批卡来定位问题。
2. 记录她的实际体验结果。通过后请求 Cubic 审查，确认最新提交的必需 CI 和 GitHub 实际分支保护，再按仓库流程合并。
3. 合并后只清理本次任务创建且确认无未提交修改的 worktree；工单 315 的 worktree 不在清理范围。
