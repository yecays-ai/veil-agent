# HANDOFF

## 当前工单 254（2026-11-08 UTC）

- PR #309 已合入 main（`ae33b69`），但 Lan 更新后点击桌宠仍看到两个黑窗。后续补丁分支为 `ticket/254-windows-console-followup`。
- PR #309 已完成快捷方式迁移、filesystem MCP 本地固定安装与 `/work` 审批路径映射；相关修复已由实机更新验证，但点击桌宠后仍发现其他服务启动控制台。
- `desktop/chat.js` 连续 3 次（约 1.8 秒）轮询失败才显示断线，成功即清零；失败次数和异常写控制台。`web_agent.py` 把未知后台异常写入现有 `data/web_service_error.txt`。
- `executor_opencode.py` 将 OpenCode 容器固定 `/work` 挂载点安全映射回主机 workdir，并跟踪复合命令中的项目内 `cd`；目录外、逃逸和新建脚本仍审批。
- 18:28 实机进程树确认新黑窗来自 Ombre 的控制台版宿主/服务；同时发现 Galatea `.cmd` MCP 仍可能退回普通 CMD。后续补丁让 Ombre 两层都用 `pythonw`，批处理 MCP 经现有无窗口代理和 `SW_HIDE` 启动。相关 162 项通过（3 项跳过）；本地批处理 MCP stdio 正常且 CMD 主窗口句柄为 0。

## 下一个窗口第一步

提交后续补丁，处理实质审查与 CI；合并前不得把自动化结果写成完整 Windows 实机验收。

## 注意

- #309 后实机进程时间证据：Ombre 宿主 18:28:47、服务 18:28:48，各自生成 conhost；Galatea CMD 18:28:51。不要再归因于 filesystem MCP 或第二套桌面入口。
- PR #303 的危险命令/搜索文本基础分类已在 main，本单只补实机暴露的复合只读、命名测试脚本、审批复用和状态闭环。
- 未安装 OpenCode，无法在当前开发目录跑真实 OpenCode API 集成；隔离 worktree也没有 Electron 依赖，正式 Windows 启动、批准/拒绝和三次任务清理必须在合并后由 Lan 验收。
- 没有改费用上限、模型、快照语义、Docker 隔离、密钥代理、手机端或自主活动专项。
