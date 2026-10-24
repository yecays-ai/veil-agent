# 当前状态

更新：2026-10-24 UTC · Codex。

## 当前目标与现状

- Reliability 五阶段已完成，运行期监测与安全恢复退出条件满足。
- PR #126（工单 082）已 squash 合并到 `main`，提交 `fb8c1bb`；Tests、Windows desktop、Dependency security 均通过。Lan 已确认桌面 Windows 体验与验收完成。
- PR #127（工单 083）已关闭，聊天恢复、上下文预算校准和 DSML 原文拦截已进入当前 `main`，提交 `366840b`。PR head `ab48b82` 的 Tests、Windows desktop、Dependency security 均通过；Lan 已确认最新桌面修改与验收完成。
- 本机 provider 上限为 32,768 tokens；上下文预算按同角色/模型观测值校准并显示分项估算。旧语音恢复按完整角色聊天账本去重；未结构化 DSML 工具协议不会作为普通回复保存或执行。
- 只读架构审计发现：核心模块之间存在循环依赖，`Chat._tool_chat` 承担多项策略协调，Chat 与 Memory 对多条 assistant 事件的轮次定义仍需明确。没有证据支持全仓重写或删除旧 extract 记忆路径。
- 本次状态同步由工单 084 / PR #128 提交；不改变程序行为。

## 下一步

如启动架构整理，先明确 Chat 与 Memory 的轮次契约并固定行为，再分别处理核心依赖边界和工具调用职责；每项独立开单，不合并成一批重构。

## 验收状态

- 工单 082 桌面 Windows 体验验收：Lan 已确认完成。
- 工单 083 聊天恢复、上下文预算与 DSML 修改验收：Lan 已确认完成。
- Reliability 五阶段退出条件：已完成。
