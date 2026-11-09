# HANDOFF

## 当前工单 321（PR 待创建，2026-11-09 15:05 +08）

- 分支 `ticket/321-investigation-alias`；独立 worktree：`E:\Peter-Agent\VEIL\worktrees\321-investigation-alias`；正式运行目录未动。
- 缺陷：`investigation_checkpoint.record()` 用模型侧别名判断只读，只读集合却是规范名；生产别名下的成功读取被记录成 `read_only: false`，续接不能正确复用新鲜证据，也会挡住过期重查。
- 修复：`record()` 新增 `canonical_name`，只读/副作用/排除判断全部用规范名；证据仍保存模型侧调用名，`resume_guard` 签名不变。`veil_agent._tool_chat` 所有记录点先解析规范名，MCP 的 `needs_checkpoint` 也用规范名。
- 联动修复：最后一步工具已收起时，模型仍输出文本工具调用会被 `raw_tool_call_text_format` 误判为 `tool_failure`；现在按 `step_limit` 记录并保留可续接检查点。
- 回归：`tests.test_tools`、`tests.test_chat`、`tests.test_investigation_checkpoint`、`tests.test_tool_aliases`、`tests.test_mcp_services` 共 209 项通过。
- Windows 真实验收：独立 `--acceptance` 实例（`max_steps=3`）、真实 deepseek、真实 `pythonw → mcp_hidden_stdio.py → node` MCP 文件工具；四场景均有执行证据。证据在 `E:\Peter-Agent\VEIL\acceptance-321\evidence\`，包括检查点、工具账本、聊天记录、模型计费账本和进程树。34 次真实模型调用，约 ¥0.105。
- 下一个窗口第一步：推送分支并创建 PR，等 CI 全绿、请求并处理 Cubic 审查；实质问题修完后合并，然后请 Lan 按 `STATUS.md` 验收。
- 未验证：其他角色、Linux 宿主、Docker 路径；本轮未重建 Docker。
