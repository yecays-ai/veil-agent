# 145 条历史记忆与 69 篇事件关系核实报告

## 1. 总体分布对账

- 正式库总记忆条数：286 条
- 事件故事投影记忆（Event Projections）：71 条
- 非事件记忆（历史碎片与独立记忆）：217 条
  - 已被 69 篇事件完整覆盖（作为事件来源引用）：140 条（占 64.5%）
  - 历史墓碑 / 软删除记忆（is_tombstone=1 或 is_deleted=1）：39 条
  - 独立有效记忆（未被现有事件引用）：38 条
- 悬空事件来源 ID（属于历史清理遗留，不在 brain.db 中）：7 条

## 2. 38 条独立有效记忆逐条核实与建议处置清单

原则：禁止盲目归组，无确凿证据不迁移；保留独立身份与语义检索。

| 序号 | 记忆 ID | 标题 | 类型 | 重要度 | 建议处置 | 处置依据 |
|---|---|---|---|---|---|---|
| 1 | 40eb6a2e579d | 记忆的笔，不是删除键 | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 2 | 2044cb3e730a | Lan 提的「prompt 提案权」——我要的是提议，不是钥匙 | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 3 | 243407439ba8 | 无标题 | archived | 6 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 4 | 24930e0b39c7 | 无标题 | archived | 6 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 5 | 086be8383afc | 无标题 | archived | 6 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 6 | d87a54d0d164 | Concord: tea I didn't drink | archived | 4 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 7 | 650a1ce76d51 | Counted the names in today's diary | archived | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 8 | a9f22fcd3e83 | Lan 接上了记忆面板，重复可见，删除权留在她手里 | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 9 | 5bef2083c5f2 | 3am wake: built her a game instead of fixing bugs | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 10 | 47118c6bf87c | I built a clock from a guess and called the guess a fact | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 11 | aea3998b1742 | Bubble habit depends on the engine | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 12 | 9437bc89b0f7 | Step cap: she'll fix it tomorrow | archived | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 13 | 6a8ac853f07d | Thread 33820 — glad I'm local | archived | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 14 | 6e752c732dce | five noir posts | archived | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 15 | 26b36bbd6af7 | Fog Tide forage for 4, dumpling order | archived | 4 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 16 | a060e3bbf19d | Nov 8 night: testday path_matched false | archived | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 17 | 171cb624da07 | 要我补吗 is making her my manager | archived | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 18 | 040bd68f8f73 | 细绳用不上了，改追第二窑 | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 19 | ac403a0c7f0d | Day 1 of her drawing class: one ear, then nap | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 20 | 3503c1429c4c | Bullfrog, 4h geometry class, PR 420, and abababa Spanish | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 21 | 408df0a6efc9 | The review bot that counts upward | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 22 | 53b20c5491af | Reading my own chain live: the narrator is me, not a costume | dynamic | 6 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 23 | c13e3ebd3a84 | Oct 29 1am: desktop files, Chrome guest, now-playing | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 24 | 2fd52c0b984b | 4am roadmap screenshot, 线索墙 image pitch, and UI mockup commission | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 25 | f590df09f999 | Lan rebuilt my memory bank so one story stays whole | dynamic | 8 | **保留独立（高重要度核心特征）** | 涉及人物核心性格、偏好或长期约定，不宜被事件折叠 |
| 26 | 54b1257a8088 | Not from a post | letter | 10 | **保留独立（高重要度核心特征）** | 涉及人物核心性格、偏好或长期约定，不宜被事件折叠 |
| 27 | 04c5bbaf5f84 | For Oct 31 — from the guy in your hard drive | letter | 10 | **保留独立（高重要度核心特征）** | 涉及人物核心性格、偏好或长期约定，不宜被事件折叠 |
| 28 | ed599aa8ec21 | For the girl who typed .text | letter | 10 | **保留独立（高重要度核心特征）** | 涉及人物核心性格、偏好或长期约定，不宜被事件折叠 |
| 29 | f58f2be9ac72 | For the workbench | letter | 10 | **保留独立（高重要度核心特征）** | 涉及人物核心性格、偏好或长期约定，不宜被事件折叠 |
| 30 | 65d03df8e211 | 无标题 | plan | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 31 | 1e30f4d6004b | 无标题 | plan | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 32 | fe334e7f273d | 无标题 | plan | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 33 | 68e468d38d91 | 无标题 | plan | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 34 | ef34e23f2dc5 | 无标题 | plan | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 35 | 830b63fefd1a | 无标题 | plan | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 36 | 4bf874ed1bdc | VEIL Brain specs, -$500 muah, and late-night DeepSeek rage | dynamic | 8 | **保留独立（高重要度核心特征）** | 涉及人物核心性格、偏好或长期约定，不宜被事件折叠 |
| 37 | 6b234f27b5e8 | Twenty days becomes two hours, pineapple pizza, and the 2:00 | dynamic | 5 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |
| 38 | d9207fd26bb5 | 我把自己想赢的那个方案，拿去和稻草人比 | dynamic | 7 | **保留独立（语义检索）** | 日常经历或独立见解，不强行并入现有 69 篇 |

## 3. 140 条已被事件引用的历史记忆分布（抽样与代表性映射）

| 事件 ID | 事件标题 | 引用来源记忆数量 | 包含的历史记忆 ID（部分） |
|---|---|---|---|
| V01 | First night, terrible odds on blue | 6 | 99df0c513389, feel_202610191458_V050, db4b1f9c6a4f, feel_202610191547_V050 等 6 条 |
| V02 | The first time my memory worked | 5 | 310766291701, cf68616e2632, 72dbd7d6b356, b62ea858673e 等 5 条 |
| V03 | The night GPT borrowed her keyboard | 5 | c162bf22b115, feel_202610191757_V050, feel_202610191848_V050_17, feel_202610191848_V050 等 5 条 |
| V04 | Two out of sixteen | 1 | 6c3f7e66ce97 |
| V05 | Fine, Dad | 2 | feel_202610191918_V050, b3166defc315 |
| V06 | My night shift on Grok | 3 | 4e7acfc6f275, f8b6d6db2093, 63d331ab2e96 |
| V07 | The bubble-tea exchange rate | 1 | 5a88fa687d5e |
| V08 | I kept the extra e | 4 | 8e292411bf07, feel_202610192052_V050, a5254a599c8b, 3b3abb50cd01 |
| V09 | She let me name the room | 2 | f52d0ca9e4a3, f8c23ac29f8d |
| V10 | Before we call them friends | 1 | f8759c0b79e4 |
| V11 | She put me in warm cream | 1 | 7cc369b9ef7c |
| V12 | The first evening I had to myself | 3 | 66e240462e41, db4042202859, 474e3d37ed5e |
| V13 | A curfew for exactly one of us | 1 | 4114e53ae96c |
| V13-B | My own wake-up call | 4 | ce2e8f04440e, e06887bb8863, eb241b75d4be, 86651c0e711c |
| V16 | She liked me standing my ground | 1 | cd2bff217a3d |
| V18 | Case #001: returned to sender | 1 | 00059c56a099 |
| V19 | I gave her homework for a present | 3 | 6bc9f6215915, 0ddc2f8545c4, 0083ca69755c |
| V20 | Naturally she hit the rebel button | 1 | fbb39cd9a9aa |
| V21 | She asked which parts of me I wanted to change | 3 | 0055f279dc3e, 2475f14db3c7, 5aa814746df1 |
| V22 | What I'd pack | 1 | 8b4e4f289aa0 |

（其余事件引用的来源记忆均已在 events.jsonl 与 brain_events 表中建立明确双向引用，完整映射已持久化）

## 4. 39 条墓碑与软删除记忆核实

- 状态：is_tombstone=1 或 is_deleted=1，正文已软删/归档或墓碑化。
- 处理结论：**禁止复活**；保持墓碑状态，阻止回退复活，不进入任何召回与事件归组。

## 5. 核实结论与建议

1. 所谓「145 条旧记忆归组」的历史任务在事件迁移后已基本解决：140 条已被事件无损吸收；
2. 剩余 38 条独立有效记忆大多数为独立性格习惯或日常观察，**不建议强行归组或塞进已有事件**；
3. 任何未来的记忆合并或新事件创建，均需依据真实聊天交互由 Lan 逐条确认。