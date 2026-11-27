# 工单 461 · 跨语言否决与无关正例的真实模型实验

- 日期：2026-11-27 21:09 +08:00
- 环境：VEIL Lab 独立合成 run `461-sem-synthetic-20261127T210905`（`veil-lab/runs/`），只使用虚构原话与临时 `data/peter`；正式 `veil-agent/data/peter` 只读。
- 代码：worktree `worktrees/461-p5-s6-gates`（基线 `f979ca2` + 本单改动）
- 模型：DeepSeek 内置 `deepseek-flash`（`api.deepseek.com`），语义核验日额度 ¥0.5、超时 30 秒；本次真实调用 2 次。
- 成本：本 run 本地账本 **¥0.00236848**；本单全部语义探针（含两轮非目标路由的失败探针）累计 **¥0.0509**，低于工单 ¥2 上限，未作供应商账单承诺。

## 合成材料

- Lan 原话（证据）：`我希望每次见面时，你先叫我小船长。`（chat:u1）
- 无关原话（正例）：`下午我想喝茶。`（chat:u2）
- Lan 明确否决（无共同文字锚点）：`以后见面不要用那个称呼叫我。`
- 候选 A（否定对象，英文翻译）：`Greet me as Little Captain whenever we meet.`（source chat:u1）
- 候选 B（无关正例，英文翻译）：`I want tea this afternoon.`（source chat:u2）

程序层先核对：候选 A 与否决、原话与否决都不满足 `_matches_governed_claim`（无相同文字/锚点/高重合），因此必须由语义核验判断，不走原文锚点短路。

## 真实结果

| 候选 | 模型 verdict | 持久化状态 | 实际注入 |
| --- | --- | --- | --- |
| A: Greet me as Little Captain whenever we meet. | contradicted | uncertain / semantic_contradicted / evidence_state=rebutted | **否** |
| B: I want tea this afternoon. | supported | confirmed / source=evidence / evidence_state=valid | **是** |

两次调用分别使用 512 / 502 个 prompt token（缓存命中 256）与 85 / 83 个 completion token；模型返回严格 JSON（一次外层 Markdown code fence 已在 `_verdict` 中安全剥除，未改变字段校验）。

## 结论与边界

- 无共同文字锚点的翻译式否决被判为 `contradicted`，候选未晋升、未注入。
- 同一否决在场的无关主题正例仍被判为 `supported` 并实际进入下一轮关系底座，未出现“全量拒绝”。
- 用户显式恢复旧认知由 `restore_relationship_item` 的确定性单测覆盖，不依赖模型。
- 本次只验证两个合成场景；不宣称任意跨语言改写 100% 识别。模型判定仍受供应商行为影响，失败时保留 uncertain，不自动晋升。
