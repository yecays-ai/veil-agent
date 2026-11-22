# 工单 419：完整故事召回、整轮记忆预算与追问冷却

格子：A（记忆与人格连续性）· 做完 Lan 能多做的一件事：问一件旧事时，Peter 能读到那件事的完整经过和结尾，而不是固定开头 600 字；预算够时同一轮可以想起多件相关的事。 · 她怎么验收：在桌面聊天里用两种自然说法问一件旧事、再问一件有后续的旧事，看回答是否包含结局；上下文页能否看到本轮记忆预算和舍弃原因。

## 目标

取消“每条故事固定 600 字符、每轮最多 2／3 条”的展示限制，改由一次算清的整轮记忆预算决定注入哪几篇、每篇给多少；预算不足时保留完整自然段并给出继续读取的 ID；明确回忆和紧接着的追问不被冷却误挡；近期对话在预算分配时有保护。

## 当前状态（返工前）

- `memory_agent.Memory.context()` 对普通聊天 `[:3]` 截候选、`len(kept) >= 3` 截注入、开场记忆 `>= 3`；判官 `MAX_PICKS = 2`（自主 3），调用处写死 `max_picks=3 if wake else 2`。
- `recall_rotation.DEFAULTS['excerpt_chars'] = 600`，`excerpt()` 直接 `text[:limit]`，会切半句话。
- 请求组装先按 token 上限裁历史（从最旧开始），再裁召回、再裁辅助上下文；记忆没有独立的整轮预算。
- 冷却只在当前这句话命中“记得／上次／当时”等词时整轮豁免；紧跟上一轮追问同一件事时会被冷却丢掉。

## 设计（预算来源、近期保护、跨阶段恢复）

### 1. 预算来源（不新增拍脑袋的字符上限）

- 复用现有 `provider.max_input_tokens` 与 `veil_agent.estimate_request_tokens` 的同一套估算（UTF-8 字节 × 校准比例 + 每条消息 framing）。
- 每轮先算 `required`：人设／用户人设／说话方式／学习记忆／系统事实（`head`）+ 工具定义 + 运行时必需块（能力通知、ongoing、时间账本）+ 当前用户消息。
- `space = max_input_tokens - required`。空间分给三块：更早的历史、记忆、可选辅助上下文（背景／便签／工作断点／内部 recall 候选）。
  - `protected` = 最近一整轮（上一条用户消息 + 它的助手回复）的实测 token；这是理解当前指代／情绪的最低保护，先扣出来。
  - 若三块需求之和不超过 `space`，各取所需。
  - 否则剩余空间按三块**实测需求比例**分配；历史不低于 `protected`。比例由测量得出，不是固定 token 数，也不新增固定字符上限。
- 预算 API 是 `Memory.finish_recall(space=... , history_tokens=... , protected_tokens=... , auxiliary_tokens=..., calibration=...)`：它返回真正要发送的 `memory.recall`／`memory.opening` 消息，并记录 `memory_used`（按实际消息计，含头、空组、指针、目录、JSON 转义）、`history_allowance`、`auxiliary_allowance`、`dropped`。`veil_agent` 先按这两个 allowance 裁历史与辅助上下文，最后才让安全网动记忆；若最终请求仍被工具结果挤超，诊断记 `final_trim`。
- 单篇默认不再截 600 字：`excerpt_chars = 0` 表示给完整正文；旧配置里显式写下的正整数继续生效（兼容），优先在自然段／句子边界切；若单句本身超出剩余额度或旧限制过小，只发送块头和继续读取指针，**不切原始文本**。

### 2. 近期对话保护

- 保护的顺序：当前用户消息 → 最近一整轮（`protected`）→ 更早历史；可选辅助上下文只在拿到自己的份额后使用。
- 历史仍从最旧一端裁；不通过删除对话制造宽裕预算。
- 不新增固定字符／token 保护值；`protected` 用最近一轮的实测 token。

### 3. 跨阶段恢复

- 候选若属于事件层里已有 `story` 的事件，**无条件**注入该事件的当前故事；完整性／权威性不能靠长度判断，合并后的故事可能更短但包含后来纠正。
- 事件没有可用 `story` 时，不同阶段各自参与，不按 `event_id` 折叠；目录行附事件 ID 与提示：用 `memory_timeline(event_id)` 读整条经历、用 `memory_read(memory_id)` 读后续阶段。
- 判官卡片同时包含标题／检索词、开头和结尾，两端各有长度预算；候选池仍有限（普通 8、判官向量另有配置），复杂问题由现有 `breath_search`／`memory_catalog`／`memory_read` 追加检索，不新建引擎、不无界增长。

### 4. 冷却

- 保留冷却防止无关闲聊反复翻旧事：普通检索／轮换／随机回忆照旧受冷却。
- 豁免：①当前这句话明确问过去（`asks_about_past`）；②判官本轮选中、且这条记忆上一轮刚被自动注入（紧接着追问同一事件）。
- 追问承接只看**紧邻当前请求的上一条用户消息**，且当前句是明确的追问短语或纯“嗯／对／继续”这类确认；显式问另一件旧事、或中间已经换了话题，都不自动 carry。
- 新开聊天不清冷却文件。重复粘贴的“复用”不再靠前 120 字猜测：无法证明整篇已在最终有效上下文时，正常按预算注入；诊断 `reused` 保留为空。
- 回复失败／取消时 `rollback_recall()` 回滚本轮冷却并恢复上一次成功注入的 `last_injected_refs`；最终再裁掉的记忆同步从 `last_injected_refs` 移除。

### 5. 诊断

`recall-diagnostics.jsonl` 每轮记录：`budget`（space／history_need／memory_need／auxiliary_need／protected／history_allowance／memory_allowance／auxiliary_allowance／memory_used／reason）、`items`（id、source、tokens、complete、paragraphs、not_shown、read_id、event、story）、`dropped`（cooldown／same_event／budget）、`budget_dropped`（`reason=budget` 的 id）、`hidden`（单独列表，不是 dropped 原因）、`final_trim`。只存 ID、原因码和计数，不存正文。上下文页显示本轮预算、注入明细、预算舍弃和最终再裁。

## 影响范围

- 直接改：`recall_budget.py`（新）、`memory_agent.py`、`recall_rotation.py`、`recall_judge.py`、`veil_agent.py`（请求组装）、判官提示词默认文本、`web_agent.py`／`desktop/chat.js` 诊断字段。
- 所有聊天入口共用 `Chat` 的请求组装与 `Memory.finish_recall`：桌面普通聊天、QQ 入站、工具后续回复、自主醒来、重启后继续都走同一处；不另建记忆引擎。
- 不改：自动写入／续写策略、事件归组质量、既有生成层日期／IP 幻觉、正式记忆库；写入阶段 600／1200 字符限制留给后续写入工单。
- 兼容：`excerpt_chars` 显式值仍生效但不再字符截断；`recall.enabled=false` 仍回到旧选择；判官关闭／故障仍按现有回退，但回退也遵守新预算与访问过滤。

## 验收步骤

1. 隔离库装入 68 篇定稿，等索引就绪；在桌面聊天用两种说法问同一件旧事，上下文页能看到完整正文与最终请求文本。
2. 问一件有后续的旧事，最终请求里能看到超过 600 字的完整故事和后半段／结尾事实。
3. 同一对话连续问同一件事再追问“那我当时是什么反应”，以及开新会话立刻问同一往事：冷却状态保留，追问不被误挡。
4. 五篇虚构独立事件：预算足够时至少注入 4 篇；降低预算后正文不被切半句，至少一篇给出完整段落 + 后续提示 + 可继续读取的 ID。
5. 无预写故事的旧碎片：经 `memory_timeline`／`memory_read` 能取回前后阶段；超长单篇能续读到结尾独有事实。
6. 无关闲聊仍可零条；隐藏／其他角色记忆不进入；判官故障回退仍守预算和访问边界。
