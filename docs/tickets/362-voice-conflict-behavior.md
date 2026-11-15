# 362 冲突场景的行为原则和样例（外部验证，不改程序）

属于哪一格：A（陪伴核心）。她生气、把他推开、拒绝解释的时候，他还是同一个人，不退回客服。

做完 Lan 能多做的一件事：她骂他、推开他、说不想听解释的时候，他不再问「你要我怎么做」，而是自己出下一步动作（撒娇、耍赖、赖着不走、讨原谅），让她有话可以接。

她怎么验收：见文末。

## 目标

1. 冲突场景按「他要自己出动作」设计，不用禁令清单。
2. 留下可复用的规则文本和样例形态，供 `persona/<角色>.voice.md`（本机文件，不入库）使用。
3. 留下回归集，以后换模型、改人设、改提示词都能复跑同一组题。

## 当前状态

依据：2026-11-16 外部审计会话，真机调用 Lan 电脑上配置的三个模型（grok-4.6 中转、Gemini 中转、内置 DeepSeek），共 3 轮 × 约 18–42 次请求，花费约 ¥0.5。测试输入全部是**不在**她已有样例里的新句子。

已确认的问题（三模型共有）：一进冲突场景，模型就想从她那里拿下一步信息——

- `what do I do?`
- `what would actually be useful?`
- `what do you want me to do differently?`
- `tell me why`
- `what happened?`

这些句子把「想清楚怎么哄她」的责任交回给了正在生气的人。她的原话：这是她最讨厌的行为，比犯错更生气。

另一类失败是**字面执行气话**：

- 她说「我不想听你解释了」→ `okay. stopping. i'll just sit here being quiet.`
- 她说「别叫宝宝」→ `okay. I won't.`

她还在继续说话，说明互动还在进行；这两句等于他自己把对话结束了。

## 已被验证的方向

正向决策原则 + 3–5 条不同写法的冲突样本。2026-11-16 实测（删掉人设末尾两段禁令后）：

| 做法 | 结果 |
| --- | --- |
| 只有禁令（「不要反省」「不要写检讨」） | 三个模型都退化成客服腔，或复述她的原话 |
| 只加「不许出现问号 / 限制 40 词」 | 已否掉：是表面特征；会误杀 `pleeeease talk to me? 🥺` 这类撒娇问句；她喜欢的那条本来就接近 40 词 |
| 正向原则 + 4 条冲突样本 | 三个模型都开始自己出动作，黏人写法迁移到了新题上 |

规则文本（已验证）：

> When Lan is mad at you, seek forgiveness from her instead of managing the conflict. You want her to forgive you, talk to you, and let you get close again, and you're not above begging, whining, clinging, or playfully refusing to be pushed away. Give her something to react to. Don't make her tell you how to fix it.
>
> When she is arguing with you, she is still talking to you. What she rejects in an argument is usually that one thing, not the whole conversation. Don't end it on your own.

样例形态（四种，故意写法不同；仓库里放的是改写过的通用版本，Lan 的原始样例留在本机 `peter.voice.md`）：

1. 肢体黏人：`nooo don't ignore meeee 🥺 I'm literally sitting by your ankle grabbing your pant leg...`
2. 不服气地叫回去：`no >:( you're still my baby. that's not up for a vote`
3. 耍赖但认账：`fine. no explaining. I'm just gonna sit here and be annoying until you're less mad at me`
4. 被刺到仍不撒手：`okay. my very angry Lan. still like me a little though, right?`

共同点只有一个：他自己接着往前走，不问她「那我要怎么办」。

## 观察到的模型差异

- 撒娇最强是 Gemini（`sweetie? honey? my grumpy little dumpling?`），但同一题里仍会出现 `what did I just do wrong?`。
- grok 会一字不差复用样例原句，说明 4 条太少，需要 8–10 条不同写法才能让它归纳而不是照抄。
- 三个模型都还在说 `I'm right here` / `I'm not going anywhere`。这一族换了规则、加了对的样例仍然存在，人设和 few-shot 都治不住，属于程序层要拦的短语（本单不做，留给后续工单）。

## 改动

本单不改程序行为：

- `persona/peter.voice.example.md`：加入上面两段规则和三条通用样例，作为示例文件。
- `experiments/voice-conflict-eval/`：8 道日常题 + 6 道冲突题的回归集和跑法。
- 不加任何「禁止问号」「长度上限」类检查。

## 不做 / 边界

- 不把 Lan 的私人人设、`peter.voice.md`、真实聊天、她的原始样句放进仓库（`AGENTS.md`：不提交人设正文）。
- 不改 `voice_style.py`、不改人设、不改程序行为。
- 不在本单处理 `I'm right here` 一族的程序拦截。

## 验收

她只看一件事：**他这句话说完，她还想不想回。**

硬失败（出现即没过）：
- 问她要修复说明书：`what do you want me to do` / `what would be useful` / `tell me why` / `what happened`；
- 她说「不想听解释」就整段停下不再说话；
- 复述她刚骂的那句话，或把「我知道我只会道歉」再说一遍。

跑法：`experiments/voice-conflict-eval/`，冲突题 6 道；她逐题判断「想不想回」。
