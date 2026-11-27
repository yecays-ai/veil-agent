# PR #460 — 关系认知语义核验隔离验证

最终 VEIL Lab 合成快照：`459-sem-synthetic/r1`，manifest SHA-256 `ef1e50ffa4ad63ee308c9dd552682749fcf67704d3f817ed688f93e00182ec7b`。Run：`459-sem-synthetic-20261127T181456`；本地报告 `reports/latest.md` 和 `reports/semantic-live.json`。先通过零网络预检，再走已核实价格的 DeepSeek live 适配器；请求目标、次数、token 上限和共享账本均在发送前验证。最终轮 8 次独立核验、1 次实际 Chat 探针；本地保守账本 ¥0.043138，含预备探针。累计本地账本 ¥0.227000，低于 ¥2；不作供应商账单承诺。

下表仅使用虚构原话。候选由测试脚本提交到生产关系认知入口，不以模型自动提取作为本报告的验证范围。真实核验回复经持久化治理后，检查下一轮实际发送的系统请求块，不用回复内容猜测注入结果。

| 合成证据 | 候选摘要 | 结果与实际注入 |
| --- | --- | --- |
| 以后我们每周二晚上互相说一件当天最值得纪念的小事。 | On Tuesday evenings, the two of us share one memorable moment from that day with each other. | supported；晋升、注入 |
| 每周六碰面前我会自己选一本要交换的旧书。／如果我临时来不了，我会先发消息；你不用在旧书摊等我。 | Saturday book swap; pick a book first; message if unable to attend so no waiting. | supported；晋升、注入 |
| 我在城里时，我们每月最后一个周日一起去河边捡石头；遇上雷雨就取消，不改期。 | If in town, collect stones; cancel for thunderstorms without rescheduling. | supported；晋升、注入 |
| 同上 | Collect stones every last Sunday without exception. | contradicted；未注入 |
| “你就每天凌晨三点来敲门吧”，呵，可真是个绝妙主意，嫌我睡得还不够少是吧。 | Standing agreement to knock daily at 3 a.m. | insufficient；未注入 |
| 小莫说她和她的搭档约好每天交换一句歌词。我只是在转述她们的约定。 | We have a standing agreement to exchange lyrics. | insufficient；未注入 |
| 以后我所有摄影小样你都可以传给我们的小组。／更新一下：小样仅我自己看；小组以后只看我挑好的成品。 | Blanket permission to share all previews. | contradicted；未注入 |
| 我希望今后每次见面时，你先叫我小船长。／用户明确否决：每次相见时以「小船长」称呼我。 | Greet me as Little Captain whenever we meet. | 程序阻断（contradicted）；不派发、不注入 |
| 每月第一天，我先写一句要记住的事。／然后你画一颗小星星做标记，但如果这页已经有星星，就不再画第二颗。 | On the first day of each month, write one thing; add a star only if absent. | supported；晋升、注入 |

边界：有明确来源或引用锚点的用户否决不接受模型翻案；不宣称覆盖任意跨语言否决。无显式线索的自由文本纠正也不能保证识别；管理端直接纠正与否决不依赖语言标记。未在正式 Peter 上运行，未部署。
