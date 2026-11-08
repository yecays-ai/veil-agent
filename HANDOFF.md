# HANDOFF

## 当前工单 254（2026-11-08 UTC）

- PR #308 已合入 main（`548cfc1`），但 Lan 的 Windows 实机验收确认桌面启动和普通测试审批仍失败。补丁分支为 `ticket/254-windows-acceptance-followup`。
- 桌面旧快捷方式仍指向 `.bat`，更新时将精确迁移到 `wscript + start-desktop.vbs`。点击桌宠后的两个黑窗已由进程树确定为 filesystem MCP 的两层 `cmd / npx`；固定 MCP 包改为安装期落到 `.deps/filesystem`，运行期由 `pythonw` 无窗口代理直接启动 Node 入口。
- `desktop/chat.js` 连续 3 次（约 1.8 秒）轮询失败才显示断线，成功即清零；失败次数和异常写控制台。`web_agent.py` 把未知后台异常写入现有 `data/web_service_error.txt`。
- `executor_opencode.py` 将 OpenCode 容器固定 `/work` 挂载点安全映射回主机 workdir，并跟踪复合命令中的项目内 `cd`；目录外、逃逸和新建脚本仍审批。
- 回归证据：执行器 54 项通过（2 项真实 OpenCode 跳过）；Web/MCP/更新器 45 项、文件工具 72 项、桌面后端/UI 16 项通过；相关 Python 文件 `py_compile` 通过。真实 filesystem MCP 集成 0.93 秒完成，运行时进程树为 `pythonw → node` 且没有 CMD；桌面 `Start Desktop.lnk` 已迁移并读回确认。

## 下一个窗口第一步

处理 PR #309 的实质审查与 CI；合并前不得把自动化结果写成完整 Windows 实机验收。

## 注意

- 实机进程时间证据：Electron 08:49:48；后端 08:49:52；两层 filesystem CMD 08:49:54 / 08:49:59；聊天 renderer 08:50:07。不要再把它解释为第二套桌面入口。
- PR #303 的危险命令/搜索文本基础分类已在 main，本单只补实机暴露的复合只读、命名测试脚本、审批复用和状态闭环。
- 未安装 OpenCode，无法在当前开发目录跑真实 OpenCode API 集成；隔离 worktree也没有 Electron 依赖，正式 Windows 启动、批准/拒绝和三次任务清理必须在合并后由 Lan 验收。
- 没有改费用上限、模型、快照语义、Docker 隔离、密钥代理、手机端或自主活动专项。
