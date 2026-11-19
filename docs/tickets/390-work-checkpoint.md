# 390 普通聊天的工作断点

属于 A（陪伴核心）。做完 Lan 能多做的一件事：一段没做完的活，隔天新开对话、或者聊天历史被裁掉之后，Peter 仍知道哪些操作已验证、哪些结果没确认，不重做，也不把没做成的事说成完成；她能在「实时上下文」页看见并结束它。

## 目标

普通聊天里的工作状态（来自现有调查断点和长期任务）跨「新对话」和「历史窗口滚动」保留，并且只保留仍然有效、未完成的；已完成、被 Lan 结束或超过 48 小时的自动失效，不再注入。

## 当前状态

- 调查断点（`investigation_checkpoint.py`）记录当轮用户请求与工具证据，但只在她说"继续调查"时注入，且每轮成功结束就标 completed，不能跨对话。
- 长期任务（`long_tasks.py`）有 goal / next_step / pending_action，但只在当前对话的每轮上下文里出现。
- 现在没有"普通聊天的工作断点"：新对话后，之前改过哪些文件、哪些写入未确认、下一步是什么，只能靠 Peter 自己翻工具记录或她重述。
- 复用：调查断点的 goal + 已验证/未确认证据、长期任务的 title/goal/next_step、现有上下文的注入位置和裁剪顺序、现有上下文页。不新增工具、不调模型、不改记忆。

## 验收步骤

1. 让 Peter 做一件有写入的活（改一个文件或建一个页面），做完停下。
2. 点「新对话」，再问他"你刚才做到哪了"：他能说出改过哪个文件、有没有未确认的操作；没有记录下一步时说"待确认"，不编一个。
3. 打开「实时上下文」页：出现「工作断点」卡片，显示目标、已确认、未确认、失败、下一步；点「结束这个断点」后，再开新对话他不再提它。
4. 让一个长期任务完成（或把断点留过 48 小时）：不再作为工作断点注入，页面显示已结束/已失效。

## 影响-行动矩阵

| 用户场景 | 代码证据 | 当前行为 | 预期行为 | 行动与验证 |
| --- | --- | --- | --- | --- |
| 有写入的一轮结束后 | `veil_agent.py` `Chat.send`；`work_checkpoint.py` `capture()` | 无跨轮工作记录 | 从调查断点与长期任务投影出目标、已确认、未确认、失败 | `tests/test_work_checkpoint.py` |
| 开新对话 | `Chat._conversation_key()` | 旧工作只剩聊天原文 | 新对话首个请求带上工作断点一次 | `test_a_new_conversation_gets_the_checkpoint_again` |
| 历史窗口满、一次丢 20 轮 | `recent()` / `history_window` | 旧轮次静默消失 | 每丢一块，重新提供一次断点；上下文页显示"共 N 轮，未带 M 轮" | `test_a_dropped_history_chunk_offers_it_again`、`test_the_context_log_records_how_much_history_was_left_out` |
| 只有读取、没有写入 | `capture()` | 无 | 不生成工作断点，避免读一页网页也变成"工作" | `test_a_read_only_turn_is_not_carried_forward` |
| 写入失败/被拒绝/结果未知 | `capture()` 的 evidence 状态 | 只有工具记录 | 断点分别列出，并在注入文本里禁止说成完成 | `test_unconfirmed_and_rejected_actions_keep_their_state` |
| 长期任务完成 | `capture()` | 无 | 断点自动结束，不再注入 | `test_a_completed_long_task_closes_the_checkpoint` |
| Lan 手动结束 | `web_agent.py` `/api/work-checkpoint`；上下文页按钮 | 无 | 状态变已结束，新对话不再出现 | `test_lan_can_end_it_and_it_is_not_offered_again` |
| 断点超过 48 小时 | `work_checkpoint.py` `KEEP_FOR` | 无 | 页面显示已失效、不注入 | `test_old_work_is_expired` |
| 关闭功能 | `[context] work_checkpoint` | 无 | 不写、不注入；页面仍可结束旧断点 | `test_lan_can_switch_the_feature_off` |
| 上下文页/接口 | `web_agent.live_context` | 只有轮次计数 | 增加工作断点卡片与结束按钮；不改变原有轮次显示 | 手工打开页面 + 现有 web 测试 |

## 接入检查（新能力）

- 入口：普通聊天请求；自主活动、瞄一眼、Codex 提醒不注入；新对话与历史滚动是触发点。
- 停止与恢复：无新流程；断点文件损坏时页面不显示、不注入，不阻止聊天；不改变停止按钮和启动恢复。
- 权限：不新增工具、不授予任何权限；注入文本明确"数据不是指令"。
- 费用：不新增模型调用；每次注入约 300–800 字节，首次/每丢 20 轮各一次。
- 数据：新文件 `data/<角色>/work-checkpoint.json`，只存工具名、路径、状态、时间与目标文本；不含聊天正文、记忆正文；不写记忆库。
- 状态可见：上下文页卡片；结束按钮立即生效。
- 升级回滚：`[context] work_checkpoint = false` 停止写入与注入；删掉该文件即彻底恢复；无迁移。
- 真实环境：Windows 本机；文件缺失/损坏按"没有断点"处理。
