# P2-B 旧记忆机制收敛审计

本表逐项核对旧机制在 Brain authority 下的去向；未验证项明确标注，不当作已完成。

| 机制 | 旧行为 | Brain authority 下的去向 | 状态/证据 |
|---|---|---|---|
| Ombre 内部衰减/自动归档 | Ombre 后台自行改桶 | 不调用 Ombre；Brain 状态只接受显式 trace/管理操作 | 停用（authority 模式）；未跑真实 Ombre 后台对照 |
| VEIL 归档/解除归档 | `_archive`/panel_unarchive 改文件 | 走 `Memory.call('trace')` → Brain state；测试覆盖 panel/archived | 保留，单测通过 |
| 合并/重复合并 | `merge_into` 归档来源 | Brain `merge_into`；sidecar merged 记录 | 保留，单测通过 |
| 事件去重 same_event/stage_cap | recall_rotation 逻辑 | 未改逻辑，读取 Brain 后仍走同一去重 | 保留，234 条 recall 测试通过；真实聊天未单独验收 |
| sidecar/versions 旧账本 | 文件 JSONL | BrainStore 将同一 API 写入 Brain 表 | 保留，单测通过 |
| 事件投影/事件 JSONL | 写入 events.jsonl | BrainStore 写 Brain 事件表；读取走 Brain | 保留，单测通过 |
| 后台经验整理 | experience_memory 写文件/Ombre | memory.store 包装到 Brain；未跑后台完整流程 | 保留，**未做完整后台验收** |
| 人工修改保护/锁定 | sidecar locked + trace 检查 | authority sidecar_state；panel/ trace 测试 | 保留，单测通过 |
| 失败恢复 | write reconciliation/重试 | Brain write_journal + vector pending 重试 | 保留，单测通过；崩溃恢复未做进程级演练 |
| 旧读路径（opening/_related/judge/page/events） | Ombre/文件 | P2-B 读取适配 | 已接；页面契约仅方法级测试，**未做前端端到端** |

结论：可明确停用的是 Ombre 后台衰减/归档；VEIL 的归档、合并、去重、锁定、失败恢复保留。
后台经验整理与真实聊天去重仍需验收，不能以本表替代。
