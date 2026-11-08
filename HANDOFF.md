# HANDOFF

## 当前工单 252（2026-11-08 UTC）

- PR #308，分支 `ticket/252-windows-opencode-reliability`，已 rebase 到最新 main `35c0aee`。范围只含 Windows 后台窗口、桌面断线去抖、OpenCode 审批分类/复用/结果回传和相邻诊断。
- `windows_process.py` 统一提供 Windows `CREATE_NO_WINDOW` 参数，已接到 OpenCode 启动/停止、Git 快照、Docker 检查/构建/清理和 Web 后台启动。`start-desktop.vbs` 是无控制台日常入口；两个旧 `.bat` 转交给它。
- `desktop/chat.js` 连续 3 次（约 1.8 秒）轮询失败才显示断线，成功即清零；失败次数和异常写控制台。`web_agent.py` 把未知后台异常写入现有 `data/web_service_error.txt`。
- `executor_opencode.py` 只放行不逃逸、不含变更参数的只读组合和任务开始前已存在于项目内的命名 Python 测试文件。审批批准仅在本次 `run()` 内按完整操作键复用，任何工具执行后失效；拒绝、取消、10 分钟超时立即 abort。回传操作只有权限类型和程序名，不含命令参数。
- 回归证据：`tests.test_executor_opencode` 运行 53 项无失败，其中 2 项真实 OpenCode 因未安装跳过；`tests.test_web` 67 项通过；rebase 后桌面 `backend.test.cjs` + `ui.test.cjs` 15 项通过；相关 Python 文件 `py_compile` 通过。

## 下一个窗口第一步

强制推送 rebase 后的 PR #308；等待 CI 与实质审查，通过后按仓库规则合并。合并前不得把自动化结果写成 Windows 实机验收。

## 注意

- PR #307 已合入 main；rebase 时只冲突了 `STATUS.md` / `HANDOFF.md`，桌面代码自动合并。表情索引和图片数据未改。
- PR #303 的危险命令/搜索文本基础分类已在 main，本单只补实机暴露的复合只读、命名测试脚本、审批复用和状态闭环。
- 未安装 OpenCode，无法在当前开发目录跑真实 OpenCode API 集成；隔离 worktree也没有 Electron 依赖，正式 Windows 启动、批准/拒绝和三次任务清理必须在合并后由 Lan 验收。
- 没有改费用上限、模型、快照语义、Docker 隔离、密钥代理、手机端或自主活动专项。
