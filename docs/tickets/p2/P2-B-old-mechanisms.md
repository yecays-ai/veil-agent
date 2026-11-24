# P2-B 旧记忆机制收敛审计

本表逐项核对旧机制在 Brain authority 下的去向；未验证项明确标注，不当作已完成。

| 机制 | 旧行为 | Brain authority 下的去向 | 状态/证据 |
|---|---|---|---|
| Ombre 内部衰减/自动归档 | Ombre 后台自行改桶 | 不调用 Ombre；Brain 状态只接受显式 trace/管理操作 | 停用（authority 模式）；隔离运行期间 vault Markdown 287 个文件数量/mtime 未变；未跑 Ombre 真实后台衰减对照 |
| VEIL 归档/解除归档 | `_archive`/panel_unarchive 改文件 | 走 `Memory.call('trace')` → Brain state；归档页改从 Brain 读 | 保留；桌面页 17/17、legacy 10/10 通过 |
| 合并/重复合并 | `merge_into` 归档来源 | Brain `merge_into`；sidecar merged 记录 | 保留；本轮修复 authority 下误用 delete 的问题，合并后来源 bucket=archive |
| 事件去重 same_event/stage_cap | recall_rotation 逻辑 | 未改逻辑，读取 Brain 后仍走同一去重 | 保留，234 条 recall 通过；连续性验收注入 ID 无重复、事件与碎片不同时注入 |
| sidecar/versions 旧账本 | 文件 JSONL | BrainStore 将同一 API 写入 Brain 表 | 保留；版本/历史/页面通过；修复 sidecar payload 缺 memory_id/kind 导致锁丢失 |
| 事件投影/事件 JSONL | 写入 events.jsonl | BrainStore 写 Brain 事件表；读取走 Brain | 保留；72/72 故事、73 阶段、事件页通过 |
| 后台经验整理 | experience_memory 写文件/Ombre | memory.store 包装到 Brain；实际触发 run_if_due | 保留；本轮实际运行并写入 Brain（journal entry/update + checkpoint），第二次同聊天 not_due 不重复 |
| 人工修改保护/锁定 | sidecar locked + trace 检查 | authority sidecar_state；panel/ trace 测试 | 保留；页面锁定/解锁通过，锁定后 Peter 改写被拒绝 |
| 失败恢复 | write reconciliation/重试 | Brain write_journal + vector pending 重试 | 保留；索引失败→pending→恢复 embedder 后 ready；写入失败回滚无半条；只读 DB 不再崩溃 |
| 旧读路径（opening/_related/judge/page/events） | Ombre/文件 | P2-B 读取适配 | 已接 Brain；实际 Electron 桌面端 17/17 通过 |
| 冻结 | 无 | `brain-write-freeze` + 跨进程锁 | 冻结后新 hold/trace 被拒绝；切换演练通过 |

结论：可明确停用的是 Ombre 后台衰减/归档；VEIL 的归档、合并、去重、锁定、失败恢复保留。
后台经验整理、真实聊天去重、页面和切换回退均已在隔离实例实机验收（见 `P2-acceptance-matrix.md`）。
