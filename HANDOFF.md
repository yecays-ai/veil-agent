# HANDOFF

## 工单 316：Windows 验收通过，Cubic 意见已处理，待最终 CI/合并（2026-11-09 08:05 +08）

- 工作分支 `ticket/316-maintenance-execution-contract`，PR #315，基于 `b7847ca`。这是工单 316，不是先前停止的工单 315（复制消息时间）。
- 工单 315 的 worktree、已有修改和运行状态保持原样；没有恢复、删除或重新测试。正式运行目录保持 `main`。
- PR #315 已从草稿转为可合并审查；本轮按 Cubic 意见修复后，最终合并由 agent 在最新 CI、实质意见和实际分支保护满足后执行。

## 本轮验收结果

- Lan 的 Windows 体验验收已通过：真实模型在隔离容器 workbench 修改 `app.py`，运行 `python3 -m unittest test_app` 退出码 0，普通测试没有误弹审批卡；模拟 PR 交付、未授权合并保护、运行中停止、重启后保持停止均符合预期。
- **本次验收不包含真实 GitHub 写入**：任务交付使用本地假 GitHub 和假 PR；不能写成真实 GitHub 交付已验证。PR #315 的合并是另一项真实 GitHub 操作。

## 本轮 Cubic 修复

- P1：代理 CONNECT/绝对 URL 只允许 HTTP/HTTPS 端口 80/443，阻止借 `host:22` 绕过 SSH/nc 边界。
- P1：代理主机 allowlist 只在明确的联网/安装命令获批后扩展；复杂命令不再顺带打开 URL 主机。
- P1：测试退出码检测覆盖 `timeout`、`pipefail` 和打印 `$?` 的包装；无法确认真实退出码时失败关闭。
- P2：自定义工作台镜像不再被默认 Dockerfile 静默覆盖；Python 3.11 的 tar 解压兼容；验收前重新核对远端 PR head；工作台 prompt 去掉仓库文件名；已停止任务在 worktree 缺失/分支不符时仍可查看；代理突发额度提高。
- P3：交接文档不再提交开发机绝对路径。
- 残余风险：测试代码与 OpenCode 同用户运行，能读取模型代理令牌，因而可调用本次模型 API（受请求上限和预算约束），不能访问任意主机。彻底隔离需要独立测试用户/网络命名空间，不在本单实现；这是“普通测试不弹审批”的已知取舍。

## 验证证据

- 最新本地专项：`python -m unittest tests.test_maintenance tests.test_executor_opencode -q` —— 143 项通过，2 项按环境跳过。
- 代理端口、主机授权范围、包装测试退出码、自定义镜像、验收 head、停止任务 worktree 缺失等新增回归通过。
- 最终提交的 GitHub 必需 CI 以 PR #315 为准；合并前必须全绿。
- 分支保护：ruleset `VEIL Main Protection` 要求 `audit`、四个 `offline-chat` 矩阵和 `scanner-report-smoke`，并要求分支与 `main` 同步；Cubic 的 `COMMENTED` review 不是 required check。

## 已知限制与风险

- 真实 GitHub 写入、真实 GitHub 账号/权限/分支保护下的任务交付仍未验证；本地假 PR 不能替代。
- 只有 HTTP/HTTPS 类命令能通过中继；`ssh`/`nc` 等非 HTTP 协议即使批准也会被防火墙拒绝。这是有意的安全限制。
- 已批准的安装/联网会打开相应主机；测试代码可读取中继令牌并调用本次模型 API，属于残余风险。
- 旧镜像必须重建；自定义镜像不会被默认 Dockerfile 覆盖，缺失时开启脚本会提示用户自行构建。
- 非容器工作台仍会在首次测试时询问一次；它没有容器网络隔离。
- 容器看门狗在宿主长时间卡顿（约 10 秒以上无法响应健康检查）时可能误停工作台。

## 下一个窗口第一步

1. 若 PR 尚未合并：检查最新必需 CI 和分支保护，确认 Cubic 实质评论已回复/解决，再由 agent 合并；不要直接推 `main`。
2. 合并后只清理本次任务创建且确认无未提交修改的 worktree；本地验收夹具目录（未跟踪）保留，工单 315 的 worktree 不在清理范围。
3. 真实 GitHub 交付需要另开验收任务，用真实测试仓库验证，不能复用本地假 PR 结论。
