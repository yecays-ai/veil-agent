# HANDOFF

## 当前工单 320（2026-11-09）

- 分支 `ticket/320-browser-continuous-execution`，工作区目录沿用 `.agents/worktrees/ticket-319-browser-continuous-execution`（历史目录名；不要另建工作区覆盖这些修改）；基于远端 `main` 的 `8b39f0d`。
- 实现：每次非维护工作台任务临时启动随机令牌保护的 MCP 浏览器网关，只暴露 `public_browser` 的 open/observe/click/scroll/fill/submit；容器 iptables 只放行本次网关端口；停止、人工接管、内部限额都会关闭网关。旧主聊天 `browser` 入口未删。
- 本次修复：快照超限死循环；提交搜索等待导航并支持 `<input type=submit>`；容器 `NO_PROXY` 加 `host.docker.internal`；移除 OpenCode 不支持的 `codemode` 配置；工作台工具说明写明可做多页公开研究；修正被 loopback 限制掩盖的网关测试断言。
- 自动检查（真实宿主已通过）：`tests.test_browser_tools` 17 项（含 5 项真实 Chromium）、`tests.test_executor_opencode` 103 项（含真实 Docker OpenCode 与新增 MCP 容器测试）、`tests.test_tools/test_web_tools/test_workbench_switch/test_tool_groups/test_maintenance` 合计 142 项；`py_compile` 与 `git diff --check` 通过。
- 真实链路已验证：Docker Desktop + `veil-workbench`（OpenCode 1.19.5）+ 宿主 Chromium，容器内 OpenCode 调用 `veil_browser_public_browser`，完成打开、填公开搜索框、提交 GET 搜索、读结果并输出报告；非自主模式不需要审批卡。未做 lan 的真实 Peter 聊天体验验收。
- 下一步：创建 PR、请求 Cubic、等待必需 CI，处理实质问题后由 agent 合并；合并后请 lan 按 `STATUS.md` 的三个体验步骤验收。真实验收发现问题就开新工单，不在本分支反复改。
- 未完成 lan 体验验收前，不得删除旧 `browser` 主聊天入口。
