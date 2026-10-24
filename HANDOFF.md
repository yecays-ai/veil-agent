# 交接

## 当前进度

- 正式仓库 `main` 已包含 PR #128 的状态同步（合并提交 `283f3be`）；没有待推送的状态文档改动。
- Reliability 五阶段已完成。
- PR #126（工单 082）已合并；Tests、Windows desktop、Dependency security 均成功，Lan 已确认桌面 Windows 体验验收完成。
- PR #127（工单 083）已关闭，代码已在 `main`；PR head `ab48b82` 的 Tests、Windows desktop、Dependency security 均成功。Lan 已确认最新桌面修改与验收完成。
- 工单 083 包含完整聊天账本语音去重、同角色/模型输入 token 校准与分项估算、原始 DSML 工具协议拦截；没有改写聊天、记忆或媒体数据。
- 只读架构审计已完成。候选整理项：核心模块循环依赖、`Chat._tool_chat` 职责集中、Chat/Memory 轮次语义需要明确定义。没有修改源码。

## 下一个窗口第一步

以 `STATUS.md` 为准。如启动架构整理，先单独明确 Chat 与 Memory 对多条 assistant 事件的轮次契约；依赖边界和 `_tool_chat` 职责拆分分别开单。

## 边界

- 正式目录保持 `main`；代码改动使用独立任务 worktree 和分支。
- 不输出或提交真实聊天内容、记忆、媒体、Persona 或密钥；上下文校准仅保存 provider 输入 token 数与请求估算字节数。
- 不降低 provider token 上限，不缩短记忆，不改成本预留或模型；界面继续标明分项为估算。
- 用户体验验收、自动 CI 和本地检查分别记录，不相互替代。
