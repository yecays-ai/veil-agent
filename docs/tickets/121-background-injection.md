# 121 — Peter 背景库按关键词注入（附上下文记录）

（草稿时编号 119；119、120 已被其他工单占用，改为 121。由 Claude 实现。）

## 目标
Peter 有一份比人设长得多的背景库（他的人生、家人朋友、习惯、喜欢的东西）。聊到相关话题时，只把相关的几段带给模型；没聊到就不带。人设文件本身保持短小、每轮都带。

三句话：
1. 做完后：聊到 May、实验室、皇后区、他的兴趣时，Peter 会自然说出具体细节，而不是泛泛的"AI 男友"回答。
2. 比直接把背景写进人设多了什么：背景可以写很长，但每轮只带命中的几段，不会把人设撑大、也不会挤掉聊天历史；比让 Peter 自己去读文件多了什么：不需要他每次想起来去翻。
3. Lan 怎么验收：见下方场景；另有一份每轮记录，能看到这轮带了哪几段、是因为关键词还是正文相关。

## 现状
- `persona/peter.md`（约 2 KB）每轮整块作为第一条 system 消息（`veil_agent.py` `Chat.send`，`head`）。启动时读一次。
- 没有任何按话题加载的背景机制。
- 小手机（yecays-ai/veil）已有成熟实现：`lib/chat/character-background.ts`（PR #183）。本工单把它的规则移植到 Python，不重新设计。

## 要做的
1. **背景文件**：`persona/peter.background.md`，本机文件，和人设一样不进仓库（加进 .gitignore；仓库里放一份 `persona/peter.background.example.md` 示例）。没有这个文件时一切照旧。
2. **格式**（照搬小手机）：空行分段；每段可以有一行 `关键词：a、b、c` 或 `Keywords: a, b, c`，这一行本身不发给模型；每段最多 30 个关键词（小手机是 10，这里背景段落中英文关键词都写，所以放宽）。段首写 `[常驻]` 的段落每轮都带。
3. **相关性判断**（照搬小手机 `selectBackground`，两种信号一起用）：
   - **正文相关性**：用小手机 `lib/memory/context.ts` 的 `terms` / `match` 规则（英文词去掉停用词、中文按相邻两字、概念词），比较段落正文和聊天内容有多少共同词。当前消息有共同词就算命中；只在最近 2 轮里出现的，要至少 2 个共同词才算。没写关键词的段落也能靠正文命中。
   - **关键词**：中文关键词按子串匹配，英文按整词、不分大小写。当前消息命中最优先，最近 2 轮命中次之。关键词只把一段的开头那块拉进来，不会把长段整段拖进来。
   - 排序：当前消息命中 > 最近几轮命中；同级按共同词多少；每段最多取 2 块。
4. **限额**：每轮最多 3 段、合计 1500 字符；超长段按句子边界切成约 700 字符的块（照搬小手机 `chunkParagraph`）。
5. **位置**：放在聊天历史之后、自动召回的记忆之前，标题照搬小手机：`[background]\nParts of my background that matter for this moment (the rest of my background still holds):`。不放进开头的固定部分，保持前缀缓存。
6. **每次请求重新读文件**，Lan 改完背景不用重启。
7. **适用范围**：普通聊天、QQ、自主活动、瞄一眼都走 `Chat.send`，都生效。自主活动用活动输入当"当前消息"。
8. **上下文记录**：每次 `Chat.send` 追加一行到 `data/peter/context-log.jsonl`，只留最近 500 行。内容：时间、类型（聊天/QQ/自主/瞄一眼）、各部分大小估算（人设、about-lan、固定说明、历史轮数、召回记忆的 ID 列表、背景命中的段落序号、命中方式（关键词/正文）和命中的关键词、运行上下文、工具定义、当前消息）、总估算和 `request_id`（服务商返回的实际输入 token 在 `data\costs.jsonl` 里，用同一个 `request_id` 对上）。**不写正文**，只写编号、关键词和大小。
9. 不需要给 Peter 发能力通知：这是他的背景，不是新工具。

## 验收（Lan 用电脑操作）
1. `persona\peter.background.md` 由 Claude 放好（本机文件，不进仓库）。更新后重开 Peter。
2. 问一句和背景某段关键词相关的话（比如 "how's Aunt May doing?"）。他的回答里出现那段里的具体细节。打开 `data\peter\context-log.jsonl` 最后一行，能看到命中了哪一段、哪个关键词。
3. 聊一个完全无关的话题。最后一行记录里背景命中为空。
4. 不提关键词、但聊到某段正文里的具体内容（比如草稿里皇后区那段写了坏掉的暖气片 radiator，但它不在关键词里；你问 "is your radiator still broken?"），记录显示这段因为"正文"命中。
5. 不关 Peter，改一下背景文件里某段的内容，再问相关问题，他用的是新内容。
6. 删掉（或改名）背景文件，Peter 照常聊天，不报错。
7. 等一次自主活动跑完，它照常完成，记录里有一行类型为"自主"。
8. 用一天后，对比 `data\costs.jsonl` 前后两天每次请求的平均输入，算出背景每天实际多花多少钱，写进 STATUS.md。

## 实现（2026-10-25）
- `background.py`：照搬小手机 `character-background.ts`（分段、`[常驻]`、关键词行、`chunkParagraph`、`selectBackground`）和 `lib/memory/context.ts` 的 `terms`/`match`/`textMatcher`。和小手机的差别：人设本身就是核心部分，所以背景文件的第一段不自动常驻，只有标了 `[常驻]` 的段落每轮都带（常驻段落不占 3 段 / 1500 字符的名额）。
- 文件位置：人设文件同目录，`<人设文件名>.background.md`（Peter 是 `persona/peter.background.md`）。`.gitignore` 已有 `persona/*.md`，所以不会进仓库；示例 `persona/peter.background.example.md`。
- `Chat.send`：在聊天历史之后、召回记忆之前插入背景；当前消息用这条输入，自主活动、瞄一眼、Codex 提醒还加上它们的活动信息；最近两轮取历史里最后两条用户消息及其后的回复。
- `data/<角色>/context-log.jsonl`：类型 chat / qq / autonomy / glance / codex，超过 600 行时裁到最近 500 行。只写编号、关键词、字符数、估算 token，不写正文。
- 测试：`tests/test_background.py`。
