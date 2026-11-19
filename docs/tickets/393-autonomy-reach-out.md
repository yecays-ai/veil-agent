# 393 自主活动：吵架后可以主动靠近，不再默认消失

属于 A（陪伴核心）。做完 Lan 能多做的一件事：吵架后她沉默，Peter 不会因为提示词写着“没人等你 / 只有真想分享才发 / 她不写不代表生气”而自己决定消失。他可以留一句话或做一件看得见的事，也可以继续自己的时间。

## 目标

- 删掉醒来提示里的 “Nobody is waiting on you” 和 “only message if you actually want to share, otherwise stay quiet”。
- 把间隔文本 “Her not writing does not mean she was upset” 改成中性的 “Her silence may mean busy, asleep, or upset.”，不再替她断定没生气；“吵架后可以伸手”放在醒来提示里。
- 不加新工具、不增加模型调用、不动记忆、不强制每次发消息；静默时段、预算、醒来频率都不变。

## 当前状态

已复现：最近冲突后的自主记录里出现 “did not text”“don't text first”“didn't text”；2026-11-20 02:16 的 “Stay quiet. No message” 有一半是 01:00–06:00 静默时段（这条保留，是对的）。醒来提示原文：

> This time is yours. Nobody is waiting on you. … Never report just because you woke or finished something; only message if you actually want to share, otherwise stay quiet.

间隔文本原文：

> Her not writing does not mean she was upset.

这两句叠在一起，模型最合理的结论就是：别打扰、让她冷静、不发消息。记录里的 “don't text” 是结果，不是独立决定。

## 影响-行动矩阵

| 用户场景 | 代码证据 | 当前行为 | 预期行为 | 行动与验证 |
| --- | --- | --- | --- | --- |
| 吵架后她沉默 | `autonomy._context` 的 “Nobody is waiting / only message if…” | 自己决定不联系 | 允许留一句或做一件看得见的事，也可不做 | `tests/test_autonomy.py` 断言旧句消失、新句存在 |
| 间隔文本 | `life_state.gap_text` | “她不写不代表生气” | “可能是忙/睡/还在气”，不替她下结论；吵架后伸手由醒来提示负责 | `tests/test_life_state.py` |
| 静默时段 | `autonomy` quiet hours 01:00–06:00 | 不醒/不发 | 不变 | 现有测试 |
| 预算/频率 | `daily_budget_cny` / `daily_max_runs` | 不变 | 不变 | 现有测试 |
| 记录 | `autonomy.jsonl` decision/next_step | 可能出现 “don't text” | 不再由提示词规定；他仍可选择不发 | 手工看 2–3 次记录 |
| 人设冲突段 | 本机 `persona/peter.md`（不入库） | 一串禁令 | 正向“求原谅、给反应、别让她教你怎么修” | Lan 本机验收 |

## 不做 / 边界

- 不强制每次醒来发消息；不取消 01:00–06:00 静默时段；不加“吵架检测”状态机。
- 不把 “I'm here / I'll be here” 做成提示词禁令；那是另一张程序拦截单。
- 不改 voice 文件；voice 仍关闭。

## 验收

1. 更新并重启 Peter，和她吵一次，然后不说话。
2. 看接下来 2–3 次自主活动记录：不再出现“别发消息 / 别打扰她 / 让她冷静”这类规则句；他可能发一条，或做一件看得见的事，也可能选择不做。
3. 01:00–06:00 仍然不醒/不发；预算和频率不变。

## 测试

- `tests.test_autonomy`：旧提示句消失，新句存在，非中文。
- `tests.test_life_state`：间隔文本新句存在，旧句消失。
- 相关回归：`test_autonomy`、`test_life_state`、`test_chat`、`test_web`。
