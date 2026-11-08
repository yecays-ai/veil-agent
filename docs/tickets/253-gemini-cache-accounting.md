# 工单 253：Gemini 缓存用量与费用核对

## 目标

Peter 连续聊天、使用工具/记忆、自主活动和花园 MCP 时，VEIL 准确记录中转站报告的缓存用量与费用，并明确标出未报告或无法核实的请求。

## 当前状态

普通聊天、工具循环、短期便签提取、自主活动、瞄一眼和委派模型复用 `veil_agent.py` 的 `make_model()`；Native Memory 通过 Ombre MCP，记忆向量召回由独立 `QueryEmbedder` 请求。费用与缓存展示由 `Budget` 写入和读取 `data/costs.jsonl`。现有上下文顺序把 persona、profile 和稳定事实放在历史之前，把每轮变化内容放在历史之后；本次未改变请求内容或工具能力。Ombre 子进程内部模型 usage 不回传 VEIL，继续按估算记账。

本次兼容 OpenAI/LiteLLM 常见缓存 usage 字段和 Gemini `usageMetadata.cachedContentTokenCount`，对缺失、无效或相互冲突的报告不虚报命中、不应用缓存折扣。记忆向量请求若响应提供合法且不超过预留的实际 token usage 则按实际用量结算；缺失、零报告或超过预留时沿用估算，按预留上限计费，不会释放负金额。字段语义参考 LiteLLM 固定提交 [`febbe38326b830d3457dd10237d805eb6cb9992b`](https://github.com/BerriAI/litellm/blob/febbe38326b830d3457dd10237d805eb6cb9992b/litellm/integrations/otel/model/payloads.py)，MIT；只借鉴字段归一化，不引入依赖。

当前没有取得 `api5.jiushi.cyou` 的真实 Gemini 响应 usage 或对应账单；因此 VEIL 的字段解析和本地结算已覆盖自动回归，Gemini 4A 实际命中是否改善及实际收费是否一致仍待验证。自动测试使用 fake transport / fake MCP，不调用付费模型。

## 验收步骤

1. 在 Windows 用 Gemini 4A 连续和 Peter 聊天，对照 `/cost` 的汇总命中率/未核实次数与代理账单；由 agent 将对应 `data/costs.jsonl` 行的 `cache_status`、`cache_usage_field`、prompt/cached/completion tokens 和金额逐项对照代理响应及账单。未报告时必须显示未报告，不能显示命中。
2. 让 Peter 使用记忆、普通工具和自主活动，确认回复、记忆和权限正常，各次 VEIL 直连请求都进入缓存统计与账本。
3. 让 Peter 通过 `galatea_garden` 连续读帖并回复，核对每次 VEIL 直连模型请求的缓存状态与账单，并与改动前相同读/回帖操作的请求数和费用基线比较；确认记账改动没有新增模型请求或费用。Ombre 子进程内部模型仍明确为估算、不可观测。