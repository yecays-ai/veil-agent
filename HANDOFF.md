# 交接

更新：2026-10-20 · Codex · 工单 022 修复实现中

## 做到哪

- 当前分支：`ticket/022-node-path`，基于已通过 GitHub API 核对的最新 main `6b44de6`。普通 GitHub fetch 因网络连接失败；GitHub 最新提交结果与本地 `main`、`origin/main` 完全一致。
- 根因已确认：运行环境里的 `shutil.which('node')` 命中 Codex 自带 Node，但 `npx` 未命中；Windows 注册表系统 PATH 包含 `E:\Program Files\`，其中 node.exe 与 npx.cmd 均存在。网页告警由这项误判触发。
- `web_launcher.py` 为分离服务进程合并当前系统/用户注册表 PATH 与继承 PATH，保留现有路径且去重；当前已有定向回归覆盖合并行为。
- 复用调查及许可证判断见 `docs/tickets/022-windows-node-path.md`；只借鉴标准库注册表 PATH 读取，不引入依赖。
- 定向单测通过；使用本机合并后的 Windows 环境确认 `node` 和 `npx` 均可定位，并运行 `cmd /c npx --version` 成功（11.19.1）。`py_compile` 和 `git diff --check` 通过；未运行全量测试或真实网页验收。

## 下一步

1. 检查改动范围，提交并推送 PR，等待 CodeRabbit 与 CI；只处理影响功能/安全的评论。
3. 合并后请 lan 按 `STATUS.md` 的 022 步骤验收。升级后需先退出当前已运行服务一次再重新双击，以确保旧服务进程环境被替换；没有 Windows 体验验收前不能标记完成。

## 注意

- 每个 PR 同步更新 `STATUS.md` 和 `HANDOFF.md`。
- 不读取或修改真实聊天、记忆、人设或费用账本，不更改模型和预算。
- 当前没有证据表明模型调用或费用记录受影响；错误发生在工具启动前的 Node/npx 能力检测。
