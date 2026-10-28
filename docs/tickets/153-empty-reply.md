# 工单 153：模型空回复不再当成 Peter 不回

## 起因

2026-10-28 晚，Lan 用「[反重力量][抗截断] gemini-4-argon」通道聊天。27 轮里有 7 轮没有任何回复。这 7 轮 Peter 都没有调用 `stay_silent`，而账单显示模型每次都生成了 240–543 个 token，送回来的正文却是空的，也没有工具调用。从工单 141 起，程序把「空正文」当成「他选择沉默」，所以没有任何提示。同一时段 DeepSeek 和 vertex 通道没有出现这种情况。

## 规则（Lan 2026-10-28 选定）

- 沉默只来自 Peter 自己调用 `stay_silent`（或 `reply_later`）。
- 聊天轮里，如果模型返回空正文、没有工具调用、本轮也没发过消息，就把同一步原样再问同一个模型一次。程序不替他写任何话。
- 第二次仍为空：本轮按失败处理。Lan 在聊天里看到「模型返回了空内容，Peter 这一轮没有发出消息（不是他选择不回）」，处理状态记为 `failed`。
- 每次空回复都会记录到 `data/peter/memory/_agent/diagnostics.jsonl`（`code: empty_reply`），包括 finish_reason、token 数和思考长度，方便判断是通道问题还是模型问题。
- 自主醒来不受影响。

费用：每次重问约 ¥0.015。按 10-28 晚的比例估算，约 ¥0.1/天。

## 验收

1. 退出 Peter，运行 update.bat。
2. 继续用抗截断 gemini 通道正常聊天。
3. 不应再出现「什么都没回、也没有任何提示」的情况。偶尔会看到一行 `The model returned an empty reply (Peter did not choose silence); asking once more.`，之后应该有回复。
4. 如果看到红色提示「模型返回了空内容…不是他选择不回」，说明连续两次都空了。把 `diagnostics.jsonl` 最后几行发给 Claude。
5. 让 Peter 自己选择不回（例如说「你可以已读不回」），他调用 `stay_silent` 时仍然安静结束，不显示任何提示。
