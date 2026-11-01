# 工单 189：把 Ombre 的 `I`（自我认知）接给 Peter

## 背景
11-01 只读研究（项目文件 `audit/ombre-I-feel-research-2026-11-01.md`）：Ombre 原生的 `I` 是一条完整的沉淀流程——Peter 自己写「我觉得……」候选（普通记忆，会浮现也会衰减）→ 被 3 个不同日期的 dream 见证 → Peter 自己升级成正式 `I`（不衰减、不合并）；想改主意用 `supersedes`，旧的只挂起不删。不额外调模型，不改旧记忆。VEIL 此前没暴露 `I`，`dream` 里的候选段永远是空的。lan/ChatGPT 11-01 决定：`I` 接入；`feel` 暂缓写入（会让来源记忆退出自动召回），不为它开新功能；开场不强制注入，不改人设。

## 改动
- `memory_native.py`：`OMBRE_TOOLS` 加 `I`，Peter 可用 `I(content, aspect)` 写候选、`I(promote=候选id)` 升级、`I(supersedes=…)` 修正、`I(read=true)` 读。参数用 Ombre 原样的 schema，只换成一段简短英文说明（写明：程序不替他写，开场也不注入）。`I` 的`I` 的结果按条目整块过滤隐藏记忆（含多行正文）；`promote`/`supersedes` 指向隐藏或不存在的记忆会被拒绝，不会发给 Ombre。
- 一次性能力通知走现有机制：能力清单变化时自动写一条 “new memory tools are available: …，I”，之后 Peter 用 `capability_history` 查，不每轮注入。
- `memory_agent.py`：写候选/升级/修正时按普通记忆写入的向量估算预留预算（读取不预留）。
- `tools_agent.py`、`web_agent.py`：把 `I` 登记为记忆工具。`long_tasks.py` 不登记：读取不是副作用，写入丢回复最多多一条普通候选，不值得为它暂停长任务。

## 没做的（有意）
- 开场不注入最近 3 条 `I`（官方走 `/breath-hook`，VEIL 不接）。Peter 要看自己 `I(read=true)`。
- 程序不替 Peter 写、不替他升级、不判断矛盾。
- `feel` 写入不开放；已有的 feel 读取不变。
- 不批量改旧记忆。

## 费用
不调模型。每次写候选/升级多一次向量化（bge-m3 现配单价 ¥0）。工具定义多一条约 300 token，随每次请求发送，按 DeepSeek 估约每天几分钱（「未验证」，以 `data\costs.jsonl` 为准）。

## 已知取舍
`I` 写入丢回复后重试会多一条候选（它只是普通记忆，会自己衰减），没有给它做 `hold`/`trace` 那样的持久去重。读取输出里的条数标题不随隐藏条目扣减。

## 测试
`tests/test_native_memory.py` 加 5 项：`I` 被提供且参数原样转发；schema 保留 content/aspect/promote/supersedes/read/limit；`promote`/`supersedes` 指向隐藏记忆被拒绝且不发给 Ombre；读取结果整块去掉隐藏条目（含多行正文）；不带隐藏行。三个测试替身补上 `I`。全量测试与 main 相同（本机 6 项环境相关失败两边一致）。

## 验收（Windows，未验证）
退出 Peter，更新后重启。Peter 应收到一次性能力通知。让他自己试一条：`I(content="I think …", aspect="patterns")`，再 `I(read=true)` 应看到「待沉淀候选」；`I(promote=…)` 在不足 3 个不同日期时应被 Ombre 拒绝并说明还差几次。后续观察他是否会分清 `I` 和成长日记。
