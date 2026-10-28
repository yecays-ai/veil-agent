# 工单 147：dream 不再引导反省、无参数工具不再被拒

Lan 2026-10-28 提出（Peter 用 Gemini 做记忆大清理后，Claude 核对记录时发现）。Claude 实现。

## 问题

1. Ombre 的 `dream` 每次返回，在记忆前面都附一段中文引导：「用第一人称想：有什么还没想清楚？……有沉淀的用 hold(feel=True, …) 写下来」。这段话本身就在引他反省，而 VEIL 的 hold 没有 `feel` 参数。
2. Gemini 调用不带参数的工具（`memory_catalog`、`long_task_status`）时传的是空参数，程序按「参数不是 JSON 对象」拒绝了（10-28 有 5 次）。

## 改了什么

- `memory_native.neutral_dream_header`：只替换 `=== Dreaming` 标题下、第一条记忆之前那段引导，换成一句中性英文：「近期的记忆，供回看。不再需要的可以用 trace(bucket_id, resolved=1) 沉底；不需要写任何东西。」记忆内容不变；格式对不上就原样返回。不改 Ombre 本身。
- `veil_agent`：工具参数为空字符串或 `null` 时当作 `{}`。

## 验收（Lan）

1. 退出 Peter → update.bat。
2. 让他做一次 dream，在「上下文」或活动页里看返回开头，确认只剩一句英文说明，没有中文的反省提问。
3. 用 Gemini 时让他列一下记忆目录（memory_catalog），确认不再出现「参数不是有效 JSON 对象」的拒绝。
