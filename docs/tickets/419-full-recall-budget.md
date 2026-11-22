# 工单 419：完整故事召回、整轮记忆预算与追问冷却

格子：A（记忆与人格连续性）· 做完 Lan 能多做的一件事：问一件旧事时，Peter 能读到那件事的完整经过和结尾，而不是固定开头 600 字；预算够时同一轮可以想起多件相关的事。 · 她怎么验收：在桌面聊天里用两种自然说法问第一次挑视频、问 Claude 后来怎么了，再连续追问同一件事；看回答是否包含结局，上下文页能否看到本轮记忆预算和舍弃原因。

## 目标

取消“每条故事固定 600 字符、每轮最多 2／3 条”的展示限制，改由一次算清的整轮记忆预算决定注入哪几篇、每篇给多少；预算不足时保留完整自然段并给出继续读取的 ID；明确回忆和紧接着的追问不被冷却误挡；近期对话在预算分配时有保护。

## 当前状态

- `memory_agent.Memory.context()` 对普通聊天 `[:3]` 截候选、`len(kept) >= 3` 截注入、开场记忆 `>= 3`；判官 `recall_judge.MAX_PICKS = 2`（自主 3），`memory_agent` 调判官时写死 `max_picks=3 if wake else 2`。
- `recall_rotation.DEFAULTS['excerpt_chars'] = 600`，`excerpt()` 直接 `text[:limit]`，会切半句话。
- 请求组装在 `veil_agent`：先按 token 上限裁历史（从最旧开始），再裁召回、再裁辅助上下文。记忆没有独立的整轮预算，各入口共用同一段组装。
- 冷却 `asks_about_past()` 只在当前这句话命中“记得／上次／当时”等词时整轮豁免；紧跟上一轮追问同一件事、句子里没有这些词时会被冷却丢掉。
- 事件层已有 `events.jsonl`（`story`、`stages`、`link_to`／`continuation_of`）与只读 `memory_timeline`／`memory_read`；旧碎片按阶段各存一条记忆。

## 设计（预算来源、近期保护、跨阶段恢复）

### 1. 预算来源（不新增拍脑袋的字符上限）

- 复用现有 `provider.max_input_tokens` 与 `veil_agent.estimate_request_tokens` 的同一套估算（UTF-8 字节 × 校准比例 + 每条消息 framing）。
- 每轮先算 `required`：人设／用户人设／说话方式／学习记忆／系统事实（`head`）+ 工具定义 + 运行时必需块（能力通知、ongoing、时间账本）+ 当前用户消息。
- `space = max_input_tokens - required`。历史与记忆分享剩下的空间：
  - `protected` = 最近一整轮（上一条用户消息 + 它的助手回复）的实测 token；这是理解当前指代／情绪的最低保护。
  - 若历史需求和记忆需求之和不超过 `space`，全部保留。
  - 否则按**实测需求比例**分配：`history_reserve = min(history_need, max(protected, round(space × history_need / (history_need + memory_need))))`，余下给记忆。比例由测量得出，不是固定 token 数。
- `Memory.context()` 接收 `allowance`（记忆可用 token）并返回 `memory_used` / `dropped`；`veil_agent` 再按 `space - memory_used` 从最旧开始裁历史，最后才裁辅助上下文。这样记忆不会在拼接后被底层静默切掉；若最终请求仍被工具结果挤超，诊断里记为 `final_trim`。
- 单篇默认不再截 600 字：`excerpt_chars = 0` 表示给完整正文；旧配置里显式写下的正整数继续生效（兼容），但改在自然段边界切，不再半句腰斩。

### 2. 近期对话保护

- 保护的顺序：当前用户消息 → 最近一整轮 → 更早历史。记忆分配前先扣掉 `protected`。
- 历史仍从最旧一端裁，保留“她为什么烦”这类紧邻前因；不通过删除对话制造宽裕预算。
- 不新增固定字符／token 保护值；`protected` 用最近一轮的实测 token。

### 3. 跨阶段恢复

- 候选若属于事件层里已有 `story` 的事件，优先注入**完整事件故事**（已审完整正文），不再只给该碎片的开头；同一事件的多条阶段按事件去重，不会删掉唯一结局。
- 事件尚无完整故事时，保留不同阶段各自参与；目录行附事件 ID 与提示：用 `memory_timeline(event_id)` 读整条经历、用 `memory_read(memory_id)` 读后续阶段。
- 判官卡片保留标题／检索词，并增加结尾一小段，让只问结尾的问题也能被选中；候选池仍有限（检索成本），但不再暗中等于“最多三条”。

### 4. 冷却

- 保留冷却防止无关闲聊反复翻旧事：普通检索／轮换／随机回忆照旧受冷却。
- 豁免：①当前这句话明确问过去（现有 `asks_about_past`）；②判官本轮选中、且这条记忆上一轮刚被自动注入（紧接着追问同一事件）。判官已判断与当前消息直接相关，不再用冷却二次否决。
- 新开聊天不清冷却文件。若同一故事本轮已出现在有效上下文里（例如历史中的 `memory_read` 结果），不重复粘贴，诊断记为 `context`；否则记为 `search`／`judge`，能解释故事从哪儿读到。
- 回复失败／取消时 `rollback_recall()` 的冷却回滚保持。

### 5. 诊断

`recall-diagnostics.jsonl` 每轮增加：`budget`（required／space／history_need／protected／memory_allowance）、`memory_used`、`items`（id、来源、token、是否整篇／段落、可继续读取的 id）、`dropped`（cooldown／same_event／budget／duplicate／hidden）和 `final_trim`。只存 ID、原因码和计数，不存正文。

## 影响范围

- 直接改：`recall_budget.py`（新）、`memory_agent.py`、`recall_rotation.py`、`recall_judge.py`、`veil_agent.py`（请求组装）、判官提示词默认文本、`memory_native.py` 的 `memory_read`／`memory_timeline` 文案（如涉及）。
- 所有聊天入口共用 `Chat` 的请求组装与 `Memory.context()`：桌面普通聊天、QQ 入站、工具后续回复、自主醒来、重启后继续都走同一处；不另建记忆引擎。
- 不改：自动写入／续写策略、事件归组质量、V78 日期／IP 生成、正式记忆库；写入阶段 600／1200 字符限制留给后续写入工单。
- 兼容：`excerpt_chars` 显式值仍生效；`recall.enabled=false` 仍回到旧选择；判官关闭／故障仍按现有回退，但回退也遵守新预算与访问过滤。

## 验收步骤

1. 隔离库装入 68 篇定稿，等索引就绪；在桌面聊天用两种说法问第一次挑视频，上下文页能看到完整 V50 与最终请求文本。
2. 问 Claude 后来怎么了，最终请求里能看到 V78 超过 600 字的完整故事与后半段 KYC／最终封号。
3. 同一对话连续问同一件事再追问“那我当时是什么反应”，以及开新会话立刻问同一往事：冷却状态保留，追问不被误挡。
4. 五篇虚构独立事件：预算足够时至少注入 4 篇；降低预算后正文不被切半句，至少一篇给出完整段落 + 后续提示 + 可继续读取的 ID。
5. 无关闲聊仍可零条；隐藏／其他角色记忆不进入；判官故障回退仍守预算和访问边界。
