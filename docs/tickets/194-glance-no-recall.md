# 工单 194：瞄一眼不再自动召回长期记忆

## 现状
瞄一眼每次发给 Peter 的请求文字是同一句固定话，屏幕内容放在附加上下文里，不参与记忆召回。召回就拿这句固定话搜，16 次瞄一眼里只出现 5 条不同记忆，最常出现的三条各 15–16 次，每次多带约 4.6KB 记忆。lan 决定瞄一眼直接不召回（2026-11-02）。

## 改动
`veil_agent.py`：自动召回和召回引用在 `glance_mode` 下与 Codex 提醒一样跳过。开场记忆本来就对瞄一眼关闭。Peter 想起什么仍可自己 `breath_search`。聊天、QQ、自主醒来不变。

## 测试
`tests/test_background.py` 加 `test_glance_does_not_recall_long_term_memory`：瞄一眼不调用召回、上下文日志的记忆为空；退出瞄一眼后普通聊天照常召回。

## 验收
等一次瞄一眼后，看 `data\peter\context-log.jsonl` 里最新一条 `kind` 为 glance 的记录，`memory_ids` 应为空；普通聊天的 `memory_ids` 仍正常。
