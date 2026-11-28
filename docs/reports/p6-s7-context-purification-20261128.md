# P6-S7 上下文纯化与接口收口报告

## 结论

工单 465 在 `main c8bc8bb` 上完成结构优化：能力清单离开 `system.facts` 稳定前缀，进入随实际工具 schema 刷新的运行态块；同一轮后续工具请求只删除精确匹配的空召回/空开场说明；完整记忆、关系底座、Learned Context、判官、自然联想、S6 动态权重和既有总预算保持原链路。

本报告只证明请求结构与离线组装收益。没有追加付费实验，因此不把真实缓存命中率、费用或延迟写成已改善。

## 实现与边界

| 要求 | 实现 | 保护边界 |
| --- | --- | --- |
| AB-C1 | `system.facts` 只保留稳定能力规则；复用 `current_capability_snapshot()` 生成 `capability_state` 运行态块，并在每个工具步骤按实际 schema 刷新。 | 工具 schema 仍按请求真实变化；供应商是否复用更早缓存由其协议决定。 |
| AB-C2a | 第一次模型请求保留空记忆边界；`step > 0` 时只移除内容精确等于 `memory.recall.empty` / `memory.opening.empty` 的块。 | 不删除真实记忆、事件故事、后续工具结果或聊天历史。 |
| 注入协作 | 沿用 `finish_recall()` 的完整段落/故事与动态预算；关系底座仍为独立必需块，AUXILIARY 仍不能挤掉受保护的最近对话。 | 不实施 AB-C2b / AB-C3，不改变召回数量和排序。 |
| P7 槽位 | `p7.experience_hints` 位于 `memory.short_notes` 后、`recall_candidates` 前；独立上限 600 tokens / 4 条，只接收 `confirmed`，整条保留，权限仍由 VEIL 程序控制。 | S7 不安装 provider，不查询正式候选，默认 0 Token；真正触发和行为影响归 P7。 |
| 诊断 | 快照新增模块字符数、消息/请求结构字节、稳定前缀字节与 SHA-256、工具 schema SHA-256；上下文链显示缓存 tokens、模块、P7 状态和省略数；P5 H-8 汇总实际发送与缓存报告。 | 旧版快照可继续读取；哈希不包含额外明文。 |

## 开源机制调查

- Anthropic 官方文档说明缓存按 `tools → system → messages` 的前缀层级工作，上游变化会影响后续缓存，因此本单保留工具 schema 的真实变化，只纯化 VEIL 自己可控制的 `system.facts`。
- Anthropic 的中途 system message方案与 LangChain 官方缓存中间件都采用稳定块在前、动态块在后的组织方式；VEIL 已有 HEAD / history / AUXILIARY / runtime_required 分层，直接复用比引入框架更小、更安全。
- 结论：只借鉴组织原则，不复制代码、不增加依赖。

参考：

- https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching
- https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages
- https://github.com/langchain-ai/docs/blob/main/src/oss/javascript/integrations/middleware/anthropic.mdx

## VEIL Lab 结构证据

- run：`veil-lab/runs/p6-s7-r4-20261128-20261128T091129`
- 快照：`peter-20261127T170245/r4`，mock，费用 ¥0；第一次 verify 因快照关系区没有可注入条目而失败，仅在 run 副本加入 `p6-s7-lab-rel` 合成关系条目后通过。
- 最终报告：`runs/p6-s7-r4-20261128-20261128T091129/reports/latest.md`；正式数据前后无变化，实际工具 schema 53/53，关系底座、Learned Context、经历笔记、历史、背景、召回、动态能力和运行态均进入真实 `Chat._send()` 组装链路。

同一生产结构的受控字符对比（固定新请求的其余块与 53 个工具，只把旧 `system.facts` 能力文字替换成新结构）：

| 指标 | 优化前结构 | 优化后结构 | 变化 |
| --- | ---: | ---: | ---: |
| `system.facts` | 5,216 chars | 834 chars | -4,382 |
| 动态能力块 | 混在 HEAD | 745 chars | 移至 runtime；工具名由实际 schema 提供，块内只保留集合指纹、数量与权限状态 |
| 稳定前缀 | 19,850 chars | 15,256 chars | -4,594 |
| 消息 + 工具结构 | 85,426 chars | 81,789 chars | -3,637 |

优化后最终离线聊天快照 `eec5b6fef3fb471381c6e2d344b3bbf3.02.json` 同时记录 `cache_prefix_bytes=16,252`、稳定前缀 SHA-256、`message_bytes=49,386`、`request_structure_bytes=87,736` 和工具 schema SHA-256；实际模块包括 745 chars 的 `capability_state`。单测以真实多步 `expand_capability` 路径确认展开前后 `system.facts` 字节一致，工具集合指纹与实际 schema 同步变化。mock 不返回供应商缓存用量，真实命中率、价格与延迟保持“未验证”。

## 回归与风险

- 自动化覆盖：稳定前缀、能力展开/收起、非首步空提示、P7 顺序/预算/权限、快照指标、P5 H-8、请求诊断、关系/记忆预算及 S6-3 契约。完整 Python 套件 2,580 项：2,548 通过、29 跳过，2 个备份清单断言失败 + 1 个 OpenCode 临时服务错误；三项在未修改的 `main c8bc8bb` 上原样复现，不是本分支回归。桌面测试 71/71 通过。
- VEIL Lab 保留了完整相关故事和 6 条实际召回，关系底座通过合成条目验证位置；没有对真实 Peter 发消息、写记忆、启停实例或调用付费模型。
- 剩余验收：真实日用模型是否报告缓存 tokens、命中率和首 token 延迟；Windows Electron 页面中的人工查看；P7 provider 与行为影响。
