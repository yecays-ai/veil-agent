# 389 自主活动跨唤醒线索

属于 A（陪伴核心）。做完 Lan 能多做的一件事：Peter 连续自主醒来四次以上，仍知道前几次实际做到哪、哪些没确认，不会把没成功的说成完成；也不会因为这份线索被迫继续旧兴趣。Lan 更新重启后，连续观察几次自主活动，再问他"你这两天在弄什么"。

## 目标

自主活动的上下文里有一份跨多次醒来的事实摘要：保存过哪些文件、每次醒来程序判定的结果、当时写下的下一步、未确认的动作；另有一份"已经读过且文件没变"的清单。默认开启，可以关闭。

## 当前状态

- 工单 387 已合并：醒来带"上次活动"六字段（decision/reason/focus/next/result/result_reason）和最近 10 个成功工具，再往前只带 2 次、每次一行。超过 3 次醒来就没有线索，只能自己调 `autonomy_history`。
- 工单 387 同时删掉了原来的提示"以上是本次醒来最新，除非要更多细节否则重读"，现在只剩"look back only if they help"。没有证据告诉他哪些文件已经读过且没变。
- 工单 388 已合并：醒来用相关记忆；本单不改记忆链路。
- 现有数据已够用：`autonomy.jsonl` 有每次醒来的结果、保存文件、下一步和未确认动作；`tools.jsonl` 有每次工具调用的时间、路径、状态和是否截断；Lab 文件有 mtime。本单不新增存储、不调用模型、不改权限和预算。
- 开源参考（always-accompany）的 `tasks.json` / `workflow_state` 用"落盘 + 每轮注入"做连续；它同时用完成校验阻止"计划了就算完成"。本单只借这个事实呈现方式，不引入它的任务框架。

## 验收步骤

1. 更新并重开 Peter，让他连续自主醒来 4 次以上（或连续几个晚上）。之后问他"你这两天在弄什么"：他能说出同一件事的进展和上次停在哪；也能说"这个做完了/我不想弄了"。
2. 看 `data/peter/autonomy.jsonl` 里最近几次：如果某次有失败或被拒绝的写入，他在下一次醒来时不应把它说成已完成；聊天或自主消息里也不应出现"我已经写好了"而实际没有。
3. 观察他醒来后是否还会反复读取同一个明确没变的文件（例如同一个 `lab/.../index.html`）；同时看他在需要实时状态时仍会查游戏/通知/窗口。
4. 把 `characters/peter/character.toml` 的 `[autonomy] threads` 改成 `false` 并重开：醒来提示里不再出现这两块，其他行为不变；改回 `true` 恢复。

## 影响-行动矩阵

| 用户场景 | 代码证据 | 当前行为 | 预期行为 | 行动与验证 |
| --- | --- | --- | --- | --- |
| 连续 4 次以上自主醒来 | `autonomy.py` `_context()` | 只有上次详细 + 前 2 次一行 | 多一份跨 6 次的事实摘要，按 Lab 项目聚合 | `thread_lines()`；`test_threads_span_more_than_the_last_two_wakes` |
| 某次工具失败却记成"完成" | `activity_view()` | 单次展示会纠正 | 摘要同样用纠正后的结果，并列出未确认动作 | `test_threads_never_report_an_unfinished_wake_as_complete`、`test_threads_show_unconfirmed_actions_instead_of_success` |
| 已读过且没变的文件 | `tools.jsonl` + Lab mtime | 每次醒来可能重读 | 列出"已读、未变"，同时说明实时状态仍要查 | `unchanged_read_lines()`；`test_unchanged_read_is_listed_only_while_the_file_is_unchanged` |
| 读到的结果是截断的 | `tools.jsonl` `result_state` | 无感知 | 不把截断读取标成"已取得足够内容" | 同上（`result_state` 过滤） |
| 关闭线索 | `character.toml` `[autonomy] threads` | 无 | 两块都不出现，其他提示不变 | `test_threads_can_be_switched_off` |
| 醒来上下文接近 32KB | `_context()` 裁剪链 | 依次丢 earlier/life/lab/travel/thoughts/previous | 先丢"已读未变"，再丢 earlier，最后才丢线索和上次活动 | 现有超长测试 + 新增块参与裁剪 |
| 普通聊天、主动消息、预算、权限 | `veil_agent.py` 调用点 | 不变 | 不变 | 只改 `_context()` 与配置解析；未新增工具或调用 |

## 接入检查（新能力）

- 入口：只在自主醒来 `AutonomyController._context()`；聊天、瞄一眼、QQ/邮件不经过。
- 状态可见：不看代码也能在 `autonomy.jsonl` 和实际对话里核对；不新增页面。
- 费用：不新增模型调用；只多约 300–600 字节提示，受现有 32KB 上限约束。
- 数据：只读 `autonomy.jsonl`、`tools.jsonl`、Lab 文件 mtime；不写新文件、不碰记忆库。
- 回滚：`[autonomy] threads = false` 即恢复旧行为；无数据迁移。
- 真实环境：Windows 本机；未知平台按路径不存在跳过。
