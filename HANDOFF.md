# HANDOFF

## 当前工单 313（2026-11-08 UTC）

- #312（Gemini 缓存费用）已合并，本工单基于其后的 `origin/main` 独立工作树开发；不要回写 #312，也不要修改正式运行目录的既有改动。
- 修改集中在 `executor_opencode.py`、`veil_agent.py`、`workbench_scope.py` 与 `tests/test_executor_opencode.py`。没有新增依赖，也没有调用真实模型。
- `TaskScope` 是一次工作台运行的内存状态：限制在选定项目根目录，记录初始测试和本次写入的测试，并缓存本任务同范围的批准或拒绝；任务结束、停止、预算或超时后即失效。
- OpenCode 原生配置统一把 Bash 请求交给 VEIL 判断。VEIL 自动放行受限项目中的读取、编辑、新建及常规测试；解释器并非按名称无条件放行，Python 脚本须是命名测试且已存在于任务开始时或由本次 OpenCode 写入。任意脚本、删除、安装、联网、推送、越界路径仍走风险判断。
- 被拒绝的外部操作回传 `user_denied`，不终止本任务内无关的安全操作；批准只表示可以执行，真实命令的成功或失败仍由 OpenCode 结果决定。
- 私有诊断写入 `<agent>/memory/_agent/workbench_permissions.jsonl`，包含原始命令、cwd、解析路径、理由、审批范围及回复；普通聊天只收到可理解的结果摘要。
- 自动回归（正常本机权限）：`python -m unittest tests.test_executor_opencode -q`，63 项通过、2 项跳过。此前受限会话因临时 Git 仓库和 loopback socket 权限出现环境错误，不代表代码失败。

## 尚未解决 / 下一窗口第一步

1. 检查 Docker 引擎与 `veil-workbench` 镜像；若可用，用 fake upstream 做一次不触及真实模型的容器路径验证。若环境不可用，明确记录为未验而非声称通过。
2. 运行 `py_compile`、差异检查和相关完整回归，清理仅本工单生成的临时目录。
3. 提交、推送 `ticket/313-workbench-task-scope` 并创建一个 PR；只处理功能、安全、费用的实质审查意见。
4. 最终 Windows 验收应覆盖实际子项目中的连续开发、一次范围批准、一次拒绝以及停止清理。正式运行目录、真实聊天、记忆、凭据与账户均未改动。
