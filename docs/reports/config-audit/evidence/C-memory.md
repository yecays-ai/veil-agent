# C · 记忆系统全局配置审计（域 3）

审计对象：`main 快照`（main = e7e7992 快照）。
只读静态取证；运行时数据 `data/`、`.env`、`brain.db` 不在快照内且禁止读正式运行目录，故**当前生效值一律"无法读取"**，只给代码默认值与 file:line 证据。
约定：character.toml = 角色配置（仓库内，git 管理）；data/<角色>/*.json = 本机设置（运行时）；代码常量 = 必须改代码；.env = 密钥。
分类码：U 用户常用 / A 高级 / S 敏感权限 / R 只读运行信息 / P 程序安全约束 / X 冗余废弃。

---

## 1. 执行链

### 1.1 启动期：角色配置装载
1. `load_character()` 读 `characters/peter/character.toml`，把 `[memory]` 整表原样放进 `cfg.memory`：`veil_agent.py:1059` `memory = dict(cfg.get('memory') or {})`；`:1060-1062` 仅在 enabled 为真时 `memory.pop('ombre_port', None)`。
2. 唯一强校验是 mode：`veil_agent.py:947-950`，`(cfg.get('memory') or {}).get('mode','native')` 非 `"native"` 直接 `raise ValueError('memory mode must be "native"')`，实例起不来。model / base_url / dimensions / input_cny_per_million **没有启动校验**。
3. `[memory] enabled` 还决定默认工具集：`veil_agent.py:1008-1015`（mode==native 且未配 [tools] 时自动 `{'enabled': False, 'folder': 'data/<id>/workspace', 'max_steps': 6, 'max_result_bytes': 8192}`，注释 "Native memory runs on the tool loop; files stay off unless [tools] says otherwise."）。
4. 记忆可用性：`veil_agent.py:6587-6589` `memory_available(cfg) = bool(cfg.memory.get('enabled')) and models_available(cfg)`。为假但 enabled 为真：CLI 打印「记忆后台需要先选择 API 模型；请打开设置选择后重启。」（`veil_agent.py:6904-6905`）；Web push「模型还没有选择，记忆后台暂未启动；请在设置里选择模型后重启。」（`web_agent.py:3734-3735`）。
5. `[memory.experience]` 只有显式存在表才构造经验记忆（`veil_agent.py:2379-2383`）；`experience_memory.load_settings` 严格校验：未知键直接拒绝（`experience_memory.py:84-104`，`raise ValueError('memory.experience has unknown keys')`）、enabled 必须 bool、day_end 必须 `%H:%M`、daily_budget_cny 必须有限且 ≥0、其余必须整数且 ≥0。
6. `[memory.events]` 宽松：只采纳"类型与默认值相同"的键，未知键静默丢弃（`event_memory.py:155-161`）；`enabled` 特判 `bool(raw.get('enabled', True))`（`:161`，表存在即默认开）。
7. `Memory.__init__`（`memory_agent.py:112-198`）：
   - 未文档化键 `[memory.brain] write_authority` 决定是否挂 Brain 写入权（`:114-119`）；生产 `backend is None` 恒挂（`:120-136`），失败 `UserError('Brain 记忆启动失败：…')`。
   - 启动迁移 `migrate_role / unhide_legacy_rows`（`:128-136`），失败拒绝启动并保留原文件。
   - 召回参数 `recall_settings(cfg.memory)`（`:181-182`）；检索参数 `:185-192`，生产强制 `retrieval_raw['backend']='brain'`（`:189`）。
   - 冷却持久化 `data/<id>/memory/_agent/recall-rotation.jsonl`（`:193-198`，读坏只当"记忆会提前回来"）。
   - 开场记忆失败 `UserError('开场记忆读取失败，请保留记忆库后重开。')`（`:214-217`）。

### 1.2 请求期：召回 → 判官 → 预算 → 注入
8. `Memory.context()`（`memory_agent.py:1458-1816`）：
   - 冷却 `:1490-1492`：`explicit = asks_about_past(query) and not (rotate or wake)`，`cooldown = 0 if explicit else recall_cfg['cooldown_turns']`。
   - 追问 `:1493` `followup = is_followup(query, recent)`；上一轮注入过的被"带过来"（`:1584-1590`）。
   - 放大池 `:1495` `widen = (rotate or wake) and recall_cfg['rotation_candidates'] > 3`；`:1522` `asked = rotation_candidates if widen else 3 + SEARCH_SPARE_HITS`（`SEARCH_SPARE_HITS = 5`，`:102`）。
   - 判官 `:1513-1517` `judge = self.judge if ... not (rotate or wake)...`，`prefetched = judge.prefetch(query)`；失败 `:1550-1558` 保留本轮原召回，wake 时 `normal, drift = list(pool), []`。
   - 去重/事件折叠 `:1591-1597` 仅 dedup 时读向量/邻接/来源，阈值 `dedup_similarity`；冷却判定 `:1650-1660`（carry 不冷却；判官刚选中且上轮展示过不冷却）。
   - 自然联想 `:1684-1740`：只在 `not wake and not widen and not explicit and not followup` 时调用 `natural_association.choose`，最多 1 条，`priority=900`（`:1799`）。
   - 开场记忆 `:1746-1762`（pinned/核心准则先出）；经历目录 `:1762-1764`；长记忆截断 `:1765,1780-1788`。
9. 整轮预算 `finish_recall()`（`memory_agent.py:1827-1966`）：`recall_budget.split(space, history_need, memory_need, protected_tokens, auxiliary_need)`（`:1885-1886`）；预算不足先取消空提示（`:1864-1868`）；拟合 3 次 + 按 priority 淘汰（`:1899-1919`）；回吐 history/auxiliary（`:1920-1921`）；`space is None` 时 `plan={'reason':'unbudgeted'}`（`:1869-1876`）。
10. `recall_budget.py`：`ratio()` 非法值落 0.4、夹 0.15..1.0（`:38-44`）；`MESSAGE_FRAMING_BYTES = 16`（`:35`）；`split()` 最近一轮完整往来是"地板"，其余按实测体量比例分（`:88-123`，注释 "The newest complete exchange is protected as a measured floor."）。
11. `recall_judge.py`：`load_settings` `:42-56`；开关文件 `:151-159`；`choose()` `:356-434`，`deadline = started + timeout_seconds + 0.5`（`:359`）；调用 `max_tokens=MAX_OUTPUT_TOKENS(200)`、`thinking='disabled'`、`request_timeout=settings['timeout_seconds']`（`:343-345`）；熔断 `:423-424`，`active()` `:171-172`；向量候选最多检查 30 条（`:318`）；跳过 `SKIPPED_TYPES=('feel','plan','letter')`（`:34`）+ archived。
12. 检索 `memory_agent.search_candidates`（`:1071-1148`）：brain.db 不存在→诊断 `fallback: FileNotFoundError`（`:1081-1083`）；每次先 `budget.estimate` 预留（`tokens = max(512, len(query))`，`:1091`），失败也按估算结算（`:1107-1113`）；异常→`brain_retrieval_fallback`，有 authority 时降级纯 BM25（`:1126-1146`，诊断 `degraded: keyword_only`），绝不回退 Ombre。BM25 打分 `brain_retrieval.py:150-195`，权重归一 `:34-43`（和为 0 回落 0.65/0.35），importance 加成 `min(0.05, max(0,(importance-5)*0.01))`（`:187-190`）；结果 <3 时随机 drift 1 条（`:241`、`:197-204`）。
13. Embedding：`recall_relevance.QueryEmbedder`（`:87-111`）。密钥 `EMBED_API_KEY` 从实例 .env 读（`memory_agent.py:1064-1067`，缺失 `UserError('missing EMBED_API_KEY')`）；base_url/model 用硬下标 `cfg.memory['base_url'] / ['model']`（`:1068`）；超时默认 20s、`max_retries=0`、输入 `text[:4000]`（`recall_relevance.py:90,99-101`）。
14. 写记忆 `memory_agent.call`（`:342-401`）：`normal_tokens = max(8192, args字节+2048)`、`reserve_tokens = 12 * normal_tokens`（`:355-360`）——一次保存最多按 12 倍预留；无后端抛 `MemoryToolError('no memory backend')`（`:393-394`）。单条上限 `memory_native._memory_limit()` = `max(cfg.max_output_tokens, cfg.tools.max_output_tokens) * 4`，为 0 即不限（`memory_native.py:292-310`；`MEMORY_CHARS = 0` §:29`）；置顶上限 §:31` `PINNED_LIMIT = 8`（文案 §:862-865、§:1122-1125`）。
15. 经验记忆 `experience_memory.py`：线程每 `TICK_SECONDS=300`（`:36`、`:160-166`）；`due()`（`:287-304`）先看体量 `chars >= min_chars and idle >= idle_minutes`→`'volume'`，`chars < floor_chars` 不动，否则日终 `local_now >= day_end 或跨天` 且 `idle >= day_end_idle_minutes`→`'day_end'`；失败退避 §:49-50` `BACKOFF=(30m,2h)`、§:306-320`；费用闸 §:549-550`（`UserError('经历整理今天的花费上限不够再发一次请求；聊天记录不受影响。')`），重试同闸 §:610-611`，到上限不检索库 §:887-889`；单批 §:371-378`（`REQUEST_CHARS=12000`、`MESSAGE_CHARS=1500`）；输出 §:44 §:MAX_OUTPUT_TOKENS=8000`；开关 §:133-145`；注入 §:1061-1102`（recent_days/recent_chars，按 事件数/日期/隐藏集 缓存 §:1072-1074`）。
16. 事件 `event_memory.py`：证据窗口 §:240,794,1522`、条数 §:809,1543`、每条字符 §:1542`；故事分批 §:49,996,1024`；§:48,486` stage_chars=0 保留整段；§:47,1179` summary_chars=0 索引整篇；§:54` `MIN_MERGE_CONFIDENCE=0.6`；§:52,340` 时间线分页。events.enabled 同时决定 memory_timeline 是否注册（`memory_native.py:272-290`）、整理是否跑 §dream_pass('tidy')`（`experience_memory.py:168-181`）、开机 §dream_pass('startup')`（`memory_native.py:283-288`）。
17. Learned Context / 关系底座：§:profile learned=true` 才用 LearnedContext，否则旧 ProfileNotes（`veil_agent.py:2310-2317`）；字节预算 §:content_bytes` 只算 facts+growth+active/uncertain ongoing+confirmed/uncertain relationship（`learned_context.py:274-285`），超限 §UserError('超出共享上限 {over} 字节（当前 {total} / {max}）；没有保存。…')`（§:287-291`）；注入 §:298-318`（growth+facts）、§:338-360`（ongoing 晚注入）；关系底座独立 system 块（`veil_agent.py:4197-4209`），超预算整条丢弃记 degraded（`relationship_base.py:114-129`；请求超长时逐条 pop `veil_agent.py:4313-4334`）；用户人设超限 §UserError('关于你（Lan 的人设）超出上限：{n} / {max} 字节。')`（`user_persona.py:34-35`）。
18. 短时便签：默认关（`veil_agent.py:1004-1007`，注释 `character.toml:234-237`）；开启才实例化（`:2370-2375`）；§:INTERVAL=30min、INACTIVE_FOR=60min、MAX_CONTEXT_NOTES=10、MAX_CONTEXT_CHARACTERS=800、MAX_NOTE_CHARACTERS=240、MAX_OPERATIONS=50、MAX_OUTPUT_TOKENS=512、MAX_REQUEST_BYTES=20000`（`short_term_notes.py:13-20`）。
19. 备份/健康：`brain-write-freeze` 存在时 hold/trace 抛 §RuntimeError('memory writes are frozen for cutover')`（`brain_authority.py:27,52-53`）；写成功但向量失败→`vector_state='pending'`，60 秒重试（§:29,77-80`）；备份/恢复 §:96,113`，仅脚本 `scripts/p2b_cutover.py:56`；导出 §:31`；离线修复 §:191-213`（`diagnose|repair|ledger-status|ledger-verify`，要求先关 VEIL）；`simplify_memory` 默认 dry-run、`--apply` 才写并先整体备份（§:1-16`）；`tag_governance` 8 类 + §DOMAIN_MAP`（§:14-26`，注释 "Free tags stop being a UI/retrieval input."）。

### 1.3 记忆页/接口链
20. `GET /api/memory/panel` → `web_agent.py:2425-2426` → §:2057-2078`（面板 + `experience.status()` + `judge.status()`；失败 503「Ombre 记忆暂时无法读取。」）。
21. `GET /api/memory/catalog|read|events|event` → §:2427-2431`（注释：Ticket 425 前经历页永远 404）。
22. `POST /api/memory/{edit,delete,versions,restore-version,archived,unarchive,lock,seen,event/split,event/move}` → §:2545-2597`；白名单 `MEMORY_CHANGE_PATHS` §:49-52`；请求体 ≤64KB（§:2548`）。
23. `POST /api/experience-memory` → §:3431-3432` → §:3151-3163` → `experience.set_enabled()`。
24. `POST /api/recall-judge` → §:3433-3434` → §:3165-3177` → `judge.set_enabled()`。
25. `POST /api/peter-page`（save_learned/save_ongoing/save_relationship/save_user_persona）→ §:3445` → §:618-683`。
26. `POST /api/instructions` → §:3439-3440` → `instructions.save()`（记忆提示词文本）。

---

## 2. 配置清单表

> character.toml 行号指 `characters/peter/character.toml`。"代码默认值"=代码里没有该键时的行为。data/*.json 当前值无法读取。

### 2.1 [memory] 主表与后端

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| memory | enabled | character.toml:37；veil_agent.py:1059-1060,6587-6589 | character.toml bool | False（`memory.get('enabled', False)`） | true/false | 仅改文件 | 总开关，压过所有子表；false 时 experience/judge/tools 全不建 | 重启 | character.toml（仓库内） | 关闭后自述变为"没有长期记忆"（veil_agent.py:1913-1916） | A |
| memory | mode | character.toml:39；veil_agent.py:947-950 | character.toml str | 'native' | **只允许 "native"**，否则启动 ValueError | 仅改文件 | 无 | 重启 | character.toml | 死键：唯一合法值（extract 模式已退役） | X |
| memory | model | character.toml:40；memory_agent.py:1068,1092,1159-1160 | character.toml str | **无默认**（硬下标） | 任意 OpenAI 兼容 embedding 模型名 | 仅改文件 | 同时决定计费行 model | 重启后首次召回 | character.toml | 缺键不启动失败，首次召回归入 except → 静默降为纯关键词 | A |
| memory | base_url | character.toml:41；memory_agent.py:1068 | character.toml str | **无默认** | http(s) URL | 仅改文件 | 无 | 重启 | character.toml | 缺/错→静默降级；密钥 .env 的 EMBED_API_KEY | A |
| memory | dimensions | character.toml:42 | 无人读取 | — | — | 代码写死，无入口 | 无 | 不生效 | character.toml | 死键：维度在 brain_retrieval.py:103 写死 `dim == 1024` | X |
| memory | input_cny_per_million | character.toml:43-45；memory_agent.py:358,1092,1159 | character.toml 字符串数字 | **无默认** | ≥0 十进制（CNY/百万 token） | 仅改文件 | 影响 embedding 预留与结算 | 重启 | character.toml | 价目不符会低估花费；注释"free model, NOT Pro/BAAI/bge-m3. Update together when switching."（:44） | S |
| memory | ombre_port（退役） | veil_agent.py:1062 | 旧 character.toml | 启动被 pop | — | 仅改文件（写了没用） | — | 立即丢弃 | 不持久 | 死键 | X |
| memory | ombre_model（退役） | memory_agent.py:57-58,362 | 旧 character.toml | 函数默认 True | bool | 仅改文件 | 只在假后端路径生效；生产走 :386 分支永不读 | 不生效 | 不持久 | 死键 | X |
| memory.brain | write_authority（未文档化） | memory_agent.py:114-119 | character.toml 嵌套表 | 无 | true 才在假后端下也启用 Brain 写权 | 仅改文件 | 生产 `backend is None` 恒为真，此键对正式环境无影响 | 重启 | character.toml | 隐性开关，注释中不存在 | S |
| memory.retrieval | weight_vector | character.toml:113；brain_retrieval.py:16-20,34-43 | character.toml float | 0.65 | 0..1，与另一权重归一 | 仅改文件 | 相加归一（:38-43） | 重启（索引缓存 key 含权重 :211-219） | character.toml | 只填一个也能工作 | A |
| memory.retrieval | weight_keyword | character.toml:114；brain_retrieval.py:16-20,34-43 | character.toml float | 0.35 | 0..1 | 仅改文件 | 同上 | 重启 | character.toml | 纯关键词降级时被强行置 1（:174-178） | A |
| memory.retrieval | backend | brain_retrieval.py:17,31-33；memory_agent.py:185-192 | character.toml 旧键 | 'brain' | 'ombre'/'brain' | 代码写死，无入口 | 生产强制改 'brain'（memory_agent.py:189） | 不生效 | character.toml | 死键 | X |

### 2.2 [memory.recall]

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| recall | enabled | character.toml:46-48；recall_rotation.py:53-61 | character.toml bool | true（仅显式 false 生效） | bool | 仅改文件 | false 会一次压平 cooldown=0/dedup=false/excerpt=0/random_chance=0/rotation_candidates=0/directory=false/association_enabled=false（:58-61） | 重启 | character.toml | 一键回旧行为，但子键被静默清零 | A |
| recall | cooldown_turns | character.toml:49；recall_rotation.py:24-40,62-65,383-384 | character.toml int | 3 | 0..10000（0=关；"kept across restarts"） | 仅改文件 | 用户明确问过去时本轮强制 0（memory_agent.py:1491-1492） | 重启 | 冷却历史在 data/<id>/memory/_agent/recall-rotation.jsonl（只存 turn/last） | 太小=同条反复出现；太大=该想起来想不起来 | U |
| recall | dedup | character.toml:50；recall_rotation.py:26,66-68 | character.toml bool | true | bool | 仅改文件 | 关闭后不读向量、不做事件折叠（memory_agent.py:1591-1597） | 重启 | character.toml | 关闭后同事件重复条目会出现 | A |
| recall | dedup_similarity | character.toml:51；recall_rotation.py:27,75-78,219-227 | character.toml float | 0.88 | 0..1 | 仅改文件 | 仅 dedup=true 时用 | 重启 | character.toml | 过高=两条都出；过低=不同事件被吞 | A |
| recall | excerpt_chars | character.toml:52；recall_rotation.py:28,62-65,87-88 | character.toml int | 0（整篇） | 0 或 1..10000；1..119 抬到 120（"shorter than this cannot carry a recognisable opening"） | 仅改文件 | 请求预算仍优先 | 重启 | character.toml | 小值=给每条记忆加硬截断（旧配置兼容） | A |
| recall | random_chance | character.toml:53；recall_rotation.py:29,75-78；natural_association.py:47-50 | character.toml float | 0.1（仅解析） | 0..1 | 仅改文件 | **唯一作用：==0 关掉自然联想** | 重启 | character.toml | 只解析不生效（注释 "legacy: still parsed, never used as a probability by ticket 422"） | X |
| recall | rotation_candidates | character.toml:54；recall_rotation.py:30,62-65；memory_agent.py:1495,1522,1544 | character.toml int | 8 | 0..10000 | 仅改文件 | >3 才开 ""widen""；也是 wake 时判官候选上限基数 | 重启 | character.toml | 调大=检索与判官输入变长（费用升） | A |
| recall | directory（未文档化） | recall_rotation.py:31,66-68；memory_agent.py:1595,1762-1764 | character.toml bool | true | bool | 仅改文件 | 关闭后无"这段经历还有哪几条"目录行 | 重启 | character.toml | 注释中不存在 | A |
| recall | directory_members（未文档化） | recall_rotation.py:32,69-71；memory_agent.py:1762 | character.toml int | 6 | 2..12（越界回默认） | 仅改文件 | 仅 directory=true | 重启 | character.toml | 越界静默回 6 | A |
| recall | stage_slots（未文档化） | recall_rotation.py:33,72-74 | character.toml int | 2 | 1..3 | 仅改文件 | 一段经历最多占几条召回名额（:10-12） | 重启 | character.toml | 调大=同一经历挤占更多位置 | A |
| recall | association_enabled | character.toml:59；recall_rotation.py:36,79-80；natural_association.py:45-46 | character.toml bool | true | bool | 仅改文件 | recall.enabled=false 或 random_chance==0 也会关掉 | 重启 | character.toml | 关掉后普通聊天无"自然浮现"那条 | U |
| recall | association_min_importance | character.toml:60；natural_association.py:24,51-53 | character.toml int | 4 | 0..10 | 仅改文件 | pinned 按 9 分算（:74-77） | 重启 | character.toml | 调高=只有很重要的记忆会自己浮现 | A |
| recall | association_cooldown_turns | character.toml:61；natural_association.py:24,54-56 | character.toml int | 8 | 0..200 | 仅改文件 | 与全局 cooldown 叠加（memory_agent.py:1706） | 重启 | character.toml | 太小=反复冒同一条 | A |
| recall | association_light_chars | character.toml:62；natural_association.py:24,57-59,63-69 | character.toml int | 40 | 8..400（字符） | 仅改文件 | 超长或工作/报错类关键词不出联想（`_NOT_LIGHT` :34-38） | 重启 | character.toml | 改变"何时能自然想起" | A |

### 2.3 [memory.recall_judge]

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| recall_judge | enabled | character.toml:70；recall_judge.py:36,46-47,152-159 | character.toml bool + data/<id>/recall-judge.json | true | bool | **页面**：记忆页「开启/关闭想起判官」（chat.js:964-970 → POST /api/recall-judge） | **页面开关压过配置**（文件有 bool 就用文件） | 下一轮请求（注释 "a file read every turn, so turning it off acts at once" :151） | data/peter/recall-judge.json `{"enabled": bool}` | 只有开/关 | U |
| recall_judge | timeout_seconds | character.toml:71；recall_judge.py:36,48-50,345,359 | character.toml float | 4.0 | 0.5..10（秒） | 仅改文件 | 页面无此项 | 重启 | character.toml | 调小=更常超时→回退旧召回 | A |
| recall_judge | ombre_candidates | character.toml:72；recall_judge.py:36,51-55 | character.toml int | 8 | 0..16（条） | 仅改文件 | 与 vector_candidates 一起构成判官输入 | 重启 | character.toml | 名称沿用 Ombre，实际是 Brain 命中数 | A |
| recall_judge | vector_candidates | character.toml:73；recall_judge.py:36,51-55,306-326 | character.toml int | 8 | 0..16；内部最多检查 30 条（:318） | 仅改文件 | 需要 query embedding（缺 EMBED_API_KEY 失效） | 重启 | character.toml | 直接决定判官 token 与费用 | A |
| recall_judge | breaker_failures | character.toml:74；recall_judge.py:36,51-55,423-424 | character.toml int | 3 | 1..20 | 仅改文件 | 页面置 true 时清零（:163-165） | 重启 | character.toml | 熔断期判官静默 | A |
| recall_judge | breaker_seconds | character.toml:75；recall_judge.py:36,51-55,424 | character.toml int | 300 | 5..3600（秒） | 仅改文件 | — | 重启 | character.toml | 只内存计时，重启清零 | A |
| recall_judge | 输出/输入上限（代码） | recall_judge.py:31-34 | 代码常量 | MAX_OUTPUT_TOKENS=200、QUERY_CHARS=600、CARD_CHARS=300、SKIPPED_TYPES=('feel','plan','letter') | 固定 | 代码写死，无入口 | — | 编译期 | 不持久 | 改必须动代码 | P |
| recall_judge | 判官提示词 | prompts/registry.py:169-175 | persona/instructions/memory.judge.*.md 覆盖 | prompts/defaults 内置 | 任意文本 | **页面**：说明页 memory 分类（instructions.py:101-108） | 用户覆盖 > 默认（prompts/__init__.py:3-5,16） | 下一轮请求 | persona/instructions/ | 改提示词影响判断质量，无校验 | A |

### 2.4 [memory.recall_context]

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| recall_context | enabled | character.toml:83-84；memory_agent.py:1046-1048 | character.toml bool | **关闭**（必须 `is True`） | true/false | 仅改文件 | 表不存在=关；注释 "OFF until min_similarity is calibrated"（:80-82） | 重启 | character.toml | 实验功能 | A |
| recall_context | min_similarity | character.toml:85；memory_agent.py:1049-1051 | character.toml float | **无默认**（缺失整表忽略） | (0,1] | 仅改文件 | 没填=不生效（"the table is ignored without a number"） | 重启 | character.toml | 未标定值会误杀/误留 | A |
| recall_context | short_chars | memory_agent.py:1052-1054 | character.toml int | 12 | ≥1（超出则不触发） | 仅改文件 | 只有消息 ≤ short_chars 才触发 | 重启 | character.toml | 中文按字算 | A |
| recall_context | rounds | memory_agent.py:1055-1056；recall_relevance.py:121-134 | character.toml int | 2 | 1..4（越界回 2） | 仅改文件 | assistant 侧截 200 字、user 侧 400 字（:128） | 重启 | character.toml | 每触发多一次 embedding 费用 | A |

### 2.5 [memory.experience]

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| experience | 整表存在性 | veil_agent.py:2379-2383 | character.toml 表 | **不存在=不启用** | — | 仅改文件 | 表在才构造 ExperienceMemory | 重启 | character.toml | 删表=功能消失，不报错 | A |
| experience | enabled | character.toml:90；experience_memory.py:63,84-94,133-140 | character.toml bool + data/<id>/experience-memory.json | false | bool（非 bool 启动失败） | **页面**：记忆页「开启/关闭自动整理」（chat.js:942-948） | **页面开关压过配置** | 下一 tick（≤5 分钟）且下次 run_if_due | data/peter/experience-memory.json | 只有开/关 | U |
| experience | min_chars | character.toml:91；experience_memory.py:63,295 | character.toml int | 3000 | ≥0 | 仅改文件 | 与 idle_minutes 同时满足才 volume | 重启 | character.toml | 调小=更频繁调用模型 | A |
| experience | idle_minutes | character.toml:92；:63,295 | character.toml int | 30（分钟） | ≥0 | 仅改文件 | 同上 | 重启 | character.toml | 调小=聊天中途整理 | A |
| experience | floor_chars | character.toml:93；:63,297-298 | character.toml int | 200 | ≥0（"less than this is never looked at, even at the end of the day"） | 仅改文件 | 优先于日终规则 | 重启 | character.toml | 低于此值夜里也不整理 | A |
| experience | day_end | character.toml:94；:63,95-96,299-303 | character.toml "HH:MM" | "23:30" | 严格 `%H:%M`，错格式启动失败 | 仅改文件 | 与跨天共同构成 day_closing | 重启 | character.toml | 本机时区 | A |
| experience | day_end_idle_minutes | character.toml:95；:63,302 | character.toml int | 10（分钟） | ≥0 | 仅改文件 | 只在 day_closing 时用 | 重启 | character.toml | 太小=刚说完就整理 | A |
| experience | daily_budget_cny | character.toml:96；:63-64,97-100,549-550,887 | character.toml 字符串/数字 | Decimal('1') | 有限且 ≥0（负数/NaN 启动失败） | 仅改文件 | 硬闸：本轮+最坏一次 > 上限不发请求；同时是事件记忆 daily_cap（:128,177） | 重启（当日已花来自 costs 账本，重启保留） | character.toml（额度）/costs.jsonl（已花） | 页面只显示上限 | S |
| experience | recent_days | character.toml:97；:63,1067-1075 | character.toml int | 3（天） | ≥0；0=不注入 | 仅改文件 | 开启才注入 | 下一轮请求（带缓存） | character.toml | 调大=每轮多带上下文 | A |
| experience | recent_chars | character.toml:98；:63,1093 | character.toml int | 1500（字符） | ≥0 | 仅改文件 | 逐条累加到上限即截断 | 下一轮请求 | character.toml | 与 recent_days 联动 | A |
| experience | 整理/修稿提示词 | prompts/registry.py:175-183 | persona/instructions 覆盖 | 内置 | 任意文本 | **页面**：说明页 memory 分类 | 用户覆盖 > 默认 | 下一轮 | persona/instructions | 影响"他会记住什么" | A |
| experience | 内部上限（代码） | experience_memory.py:36-62 | 代码常量 | TICK_SECONDS=300、MAX_ENTRIES=2、MAX_UPDATES=3、ENTRY_CHARS=0、UPDATE_CHARS=0、MESSAGE_CHARS=1500、REQUEST_CHARS=12000、MAX_OUTPUT_TOKENS=8000、FAILURES_KEPT=20、FAILURE_CHARS=4000、BACKOFF=(30m,2h)、CANDIDATES=12、LIBRARY_QUERIES=4、LIBRARY_QUERY_CHARS=800、LIBRARY_HITS=6、LOOKS=2、STAGE_HITS=4、OWN_SAVES=3、CANDIDATE_CHARS=6000、VERBATIM_RUN=24、BACKFILL=3天 | 固定 | 代码写死，无入口 | — | 编译期 | 不持久 | "一次最多写 2 条""一次请求 12000 字"等用户可感知预算不可配 | P |

### 2.6 [memory.events]

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| events | enabled | character.toml:104；event_memory.py:43,161；memory_native.py:274-281；experience_memory.py:170-172 | character.toml bool | DEFAULTS False，但表存在时 `bool(raw.get('enabled', True))` → **实际默认 true** | bool | 仅改文件 | 决定 memory_timeline 注册、整理跑事件维护、开机恢复 | 重启 | character.toml | 关闭后账本仍在但不再归组/更新 | A |
| events | stage_chars | character.toml:105；event_memory.py:48,486 | character.toml int | 0（整段保留） | ≥0；0=不限 | 仅改文件 | 非 0 才截断 | 重启 | character.toml | 0 时无作用（默认） | A |
| events | summary_chars | character.toml:106；:47,1179 | character.toml int | 0（索引整篇） | ≥0；0=不限 | 仅改文件 | 聊天召回另受 recall_budget 限制 | 重启 | character.toml | 调小=故事被截断后写入检索 | A |
| events | story_batch_chars | character.toml:107；:49,996,1024 | character.toml int | 12000（字符/批） | ≥0（0 回 12000） | 仅改文件 | 决定长故事分几次合并 | 重启 | character.toml | 调小=多次模型调用（更贵） | A |
| events | timeline_page | character.toml:108；:52,340 | character.toml int | 20（条/页） | ≥1 | 仅改文件 | memory_timeline 默认分页 | 重启 | character.toml | 只影响浏览 | U |
| events | evidence_minutes | character.toml:109；:44,240,794,1522 | character.toml int | 30（分钟） | ≥1（内部 max(1,…)） | 仅改文件 | 决定"前后多久算同一次经历的证据" | 重启 | character.toml | 调大=易把无关事并进来 | A |
| events | evidence_items（未文档化） | event_memory.py:45,809,1543 | character.toml int | 8（条） | ≥1（内部 max(1,…)） | 仅改文件 | 与 evidence_chars 一起限证据量 | 重启 | character.toml | 注释未列 | A |
| events | evidence_chars（未文档化） | event_memory.py:46,1542 | character.toml int | 400（字符/条） | ≥0 | 仅改文件 | 每条证据截断 | 重启 | character.toml | 注释未列 | A |
| events | search_query_chars（未文档化） | event_memory.py:50 | character.toml int | 2000（字符） | ≥0 | 仅改文件 | embedding 查询可短于 stage（"grouping sees it all"） | 重启 | character.toml | 注释未列 | A |
| events | investigate_rows（未文档化） | event_memory.py:51,781 | character.toml int | 400（行） | ≥1 | 仅改文件 | 调查回读账本行数 | 重启 | character.toml | 调大=内存/IO 变大 | A |
| events | MIN_MERGE_CONFIDENCE | event_memory.py:54 | 代码常量 | 0.6 | 固定 | 代码写死，无入口 | 低于此值不合并，改判可复核事件 | 编译期 | 不持久 | 合并策略不可调 | P |

### 2.7 Learned Context / 关系底座 / 用户人设

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| profile | file | character.toml:166；veil_agent.py:1066-1073 | character.toml 文件名 | 无（必填） | 同目录文件名，.md；learned=true 时允许 .json | 仅改文件 | 决定读写哪份文件 | 重启 | character.toml | 路径穿越被拒（`Path(name).name != name`） | S |
| profile | learned | character.toml:167；veil_agent.py:1067-1069,2311-2317 | character.toml bool | false | bool | 仅改文件 | true=LearnedContext，false=ProfileNotes（旧 about-lan.md） | 重启 | character.toml | 两种实现页面/接口不同 | A |
| profile | legacy_file | character.toml:168；veil_agent.py:1077-1081 | character.toml .md 文件名 | 'about-lan.md' | 必须 .md 文件名 | 仅改文件 | 只作首次迁移来源（learned_context.py:66-70,82-89） | 重启；仅在目标文件不存在时迁移 | character.toml | 改错=迁移不到旧笔记 | A |
| profile | max_bytes | character.toml:169；veil_agent.py:1074；learned_context.py:39,58,287-291 | character.toml int | **3000**（veil_agent.py:1074）／LearnedContext 自身 4000 | ≥1 字节（UTF-8 合计） | **页面只读显示** Peter 页「已用 / 上限」（peter-page.js:310-311）；改值仅改文件 | facts+growth+active/uncertain ongoing+confirmed/uncertain relationship 共享（learned_context.py:274-285） | 重启读额度；保存时校验 | character.toml | 超限拒绝保存并提示删减；outdated/completed 不计 | S |
| profile.relationship_base | enabled | character.toml:174-175；relationship_base.py:24,38-39；veil_agent.py:1082-1085 | character.toml bool | true | bool | 仅改文件 | 关闭后不注入关系底座 | 重启 | character.toml | 表必须嵌在 [profile] 内，否则被忽略 | A |
| profile.relationship_base | max_chars | character.toml:176；relationship_base.py:24,40-43,122 | character.toml int | 480（字符） | 80..4000（越界回默认不报错） | 仅改文件 | 与请求级淘汰叠加（veil_agent.py:4313-4334） | 下一轮请求 | character.toml | 整条保留不切句，放不下记 degraded/dropped | A |
| profile.relationship_base | max_items | character.toml:177；relationship_base.py:24,40-43,114-116 | character.toml int | 8（条） | 1..40 | 仅改文件 | 优先级 confirmed>uncertain；lan>evidence>peter；同档新优先（:69-76） | 下一轮请求 | character.toml | 超条数整条丢弃并记录 | A |
| profile | 注入块 | veil_agent.py:4193-4209 | 请求内 system 消息 | 空则不注入 | — | 代码写死，无入口 | learned 在 persona 后；relationship_base 独立块 | 下一轮请求 | 不持久 | 超长先删关系底座（:4313-4334） | R |
| user_persona | file | character.toml:185；veil_agent.py:1088-1091,2323-2325 | character.toml .md 文件名 | 无（必填） | 必须 .md 文件名 | **页面**：Peter 页「关于你」 | — | 下一轮请求 | characters/peter/lan.md | 路径穿越被拒 | U |
| user_persona | max_bytes | character.toml:186；veil_agent.py:1092-1093；user_persona.py:17,34-35 | character.toml int | 4000 | ≥1 字节 | 仅改文件（页面只见 bytes） | 独立预算，不占 profile 4000 | 重启 | character.toml | 超限抛"超出上限：n / max 字节" | S |

### 2.8 工具/接口层上限

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| memory tools | [tools] max_result_bytes | character.toml:128；memory_native.py:258；memory_agent.py:1014-1015 | character.toml int | 8192 | 整数（字节） | 仅改文件 | 工具返回截断；catalog 分页受它约束（:726） | 重启 | character.toml | 太小=目录"一条都放不下"（:729-730） | A |
| memory tools | [tools] max_output_tokens | character.toml:117；memory_native.py:300-305 | character.toml int | 4096 | 整数 | 仅改文件 | 与模型 max_output_tokens 取大 ×4 = 单条记忆上限 | 重启 | character.toml | 调大=允许更长记忆 | A |
| memory tools | PINNED_LIMIT | memory_native.py:31,862-865,1122-1125 | 代码常量 | 8 | 固定 | 代码写死，无入口 | — | 编译期 | 不持久 | 想多置顶必须改代码；文案写死 "8 memories are already pinned" | P |
| memory tools | CONTENT/TITLE/WHY_LIMIT | memory_native.py:25,592-596 | 代码常量 | 1500 / 100 / 500 | 固定 | 代码写死，无入口 | **仅页面编辑与 trace 校验**；Peter 自己 hold 走 _memory_limit | 编译期 | 不持久 | 页面比 Peter 自写更严，属不一致 | P |
| memory tools | MEMORY_CHARS | memory_native.py:29,292-310 | 代码常量 | 0（按 token 推算） | 固定 | 代码写死，无入口 | 0 时上限=max(model.tools)×4 字符 | 编译期 | 不持久 | 两个 token 都没配→0→**完全不限长** | P |
| memory tools | CATALOG_PAGE | memory_native.py:32,720-731 | 代码常量 | 5（条/页） | 固定 | 代码写死，无入口 | 与 max_result_bytes 共同分页 | 编译期 | 不持久 | 不可调 | P |
| memory tools | MERGED_MEMORY_LIMIT_BYTES | memory_native.py:33,505 | 代码常量 | 48KB | 固定 | 代码写死，无入口 | 合并前检查 | 编译期 | 不持久 | 超限拒绝合并 | P |
| memory tools | memory_read 分页 | memory_native.py:735-759 | 代码常量 | 每次 1000 字符 | 固定 | 代码写死，无入口 | offset/version 必须匹配 | 请求时 | 不持久 | 防乱读 | P |
| memory tools | SEARCH_RESULTS_LIMIT | memory_native.py:1025 | 代码常量 | 20 | 固定 | 代码写死，无入口 | 只有被展示过的记忆能被改写（:1027-1032） | 编译期 | 不持久 | 权限白名单 | P |
| memory tools | 删除确认 | memory_native.py:862-870,1012-1023 | 交互输入 | 需输入 y | 必须 y | 工具（聊天）/ 页面两击（chat.js:1037-1038） | 页面 panel_delete 不再问 y | 立即 | brain.db + 台账 | Peter 自删必须人确认 | S |
| memory 诊断 | RECALL_DIAGNOSTICS_KEEP | memory_agent.py:25,959-976 | 代码常量 | 200 行 | 固定 | 代码写死，无入口 | 超过 200+40 行裁到 200 | 每轮追加 | data/<id>/recall-diagnostics.jsonl | 只存 id/原因不存正文（:960） | R |
| memory 诊断 | panel 回看条数 | memory_native.py:435-444 | 代码常量 | 最近 100 次请求 | 固定 | 代码写死，无入口 | 用于"被想起过几次" | 打开记忆页 | 不持久 | 只读 | R |
| 冷却历史 | HISTORY_LIMIT | recall_rotation.py:43,386-391 | 代码常量 | 400 条 id | 固定 | 代码写死，无入口 | 超限丢最旧 | 每轮 | data/<id>/memory/_agent/recall-rotation.jsonl | 只存 id 与 turn（:364） | R |
| 联想诊断 | DROP_LIST_LIMIT / DRIFT_PENALTY | natural_association.py:27,29,96-99 | 代码常量 | 40 / 20 | 固定 | 代码写死，无入口 | 诊断可读性与打分惩罚 | 编译期 | 不持久 | 只读 | R |
| 经验链 | experience_chain limit | recall_rotation.py:230-240 | 代码常量 | 12 | 固定 | 代码写死，无入口 | 同一经历最多连 12 条 | 请求时 | 不持久 | 更长的经历只按前 12 条判定 | P |
| 预算校准 | recall_budget.ratio | recall_budget.py:35,38-44 | 代码常量 | 默认 0.4，夹 0.15..1.0 | 固定 | 代码写死，无入口 | 所有 token 估算共用（veil_agent.py:4307-4308） | 请求时 | 不持久 | 估算偏差影响全部预算 | P |
| 上下文预算 | protected_recent_tokens | recall_budget.py:64-85 | 代码常量 | 最近一轮完整往来 | 固定 | 代码写死，无入口 | 保护地板，先于记忆分配 | 请求时 | 不持久 | 保证"刚说的话"不被挤掉 | P |

### 2.9 短时便签 / 背景资料

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| short_term_notes | enabled | character.toml:237；veil_agent.py:1000-1007,2370-2375 | character.toml bool | false（注释 "off by default (Lan 2026-11-02: no visible benefit, only cost)"） | bool | 仅改文件 | 关闭时连模块都不导入 | 重启 | character.toml | 开启即有额外调用与费用 | U |
| short_term_notes | 内部上限 | short_term_notes.py:13-20 | 代码常量 | INTERVAL=30min、INACTIVE_FOR=60min、MAX_CONTEXT_NOTES=10、MAX_CONTEXT_CHARACTERS=800、MAX_NOTE_CHARACTERS=240、MAX_OPERATIONS=50、MAX_OUTPUT_TOKENS=512、MAX_REQUEST_BYTES=20000 | 固定 | 代码写死，无入口 | — | 编译期 | 不持久 | 全部不可配 | P |
| background | LIMITS | background.py:16-17 | 代码常量 | items=3, characters=1500, chunk=700, chunks_per_paragraph=2, keywords=30, keyword_characters=40 | 固定 | 代码写死，无入口 | 每轮重读 persona/<role>.background.md（:5-8） | 下一轮请求 | 不持久 | 段数/字数不可配 | P |
| recent_experiences | MAX_ITEMS/MAX_CONTEXT_BYTES | recent_experiences.py:8-9 | 代码常量 | 5 / 18000 | 固定 | 代码写死，无入口 | 瞄一眼记录注入 | 请求时 | 不持久 | 默认 5 条 | P |

### 2.10 备份 / 迁移 / 健康维护

| 模块 | 配置项 | 源码位置 | 存储/格式 | 代码默认值 | 合法值/范围 | 修改方式/入口 | 优先级/覆盖 | 生效时机 | 持久化 | 风险/权限 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| brain_authority | 写入冻结标记 | brain_authority.py:27,52-53 | data/<id>/brain-write-freeze（存在即冻结） | 不存在 | 手工建/删 | 仅改文件（无页面） | 压过一切：hold/trace 直接 RuntimeError | 立即（每次写检查） | 文件本身 | 误建文件=他记不住任何事 | S |
| brain_authority | 待补向量重试 | brain_authority.py:29,77-80 | 代码常量 | 60 秒 | 固定 | 代码写死，无入口 | — | 每次写成功后 | DB 里 vector_state='pending' | 只重试不丢数据 | P |
| brain_backup | 备份/恢复 | brain_backup.py:96,113；scripts/p2b_cutover.py:56 | 命令行 | 无配置键 | §backup(db,out) / restore(backup,db)§ | **仅脚本**，无页面 | 恢复前校验 quick_check+digest+计数（:37-55） | 手动 | 备份文件（用户指定） | 直接改 brain.db，S 级破坏性 | S |
| brain_export | 导出 | brain_export.py:31-60 | 命令行 | 无配置键 | §export_vault(db,out,include_events=True)§ | **仅脚本**，无页面 | 只读 brain.db | 手动 | 导出目录 | 不修改 brain.db（:4-5） | R |
| brain_migrate | 启动迁移 | memory_agent.py:128-136；brain_migrate.py | 自动 | 每次启动跑 | 无需配置 | 代码写死，无入口 | 失败即拒绝启动 | 每次启动 | brain.db + 迁移账本 | 报错要求保留原文件 | P |
| data_repair | 离线修复 | data_repair.py:191-213 | 命令行 | 子命令 diagnose/repair/ledger-status/ledger-verify | --file 必须是 data 下 .jsonl | **仅命令行**（离线，需关 VEIL） | 修复前保存原文件、损坏行进隔离 | 手动 | 原文件 + 隔离文件 | S 级：改聊天/费用账本 | S |
| simplify_memory | 一次性清理 | simplify_memory.py:1-16 | 命令行 | 默认 dry-run | --apply 才写；先整体备份 | **仅命令行** | 需 data/session.lock 不被占用 | 手动 | data/<role>/backups/… | 删 feel/I 与 embeddings.db 行，S 级 | S |
| tag_governance | 分类字段 | tag_governance.py:14-26 | 命令行 | 8 类 + DOMAIN_MAP | 固定映射 | **仅命令行** | 原标签留迁移表可回滚 | 手动 | brain.db | 注释 "memory body, IDs, sources, events and versions are never touched." | S |
| maintenance | VEIL 自维护 | maintenance.py:1-10 | 命令行/工具 | 与记忆无关 | — | — | — | — | — | 本域不涉及（已核对） | X |

---

## 3. 重复 / 冲突 / 隐性覆盖

1. **同一开关两层来源，页面值压过配置文件**：判官 `[memory.recall_judge].enabled` 被 `data/peter/recall-judge.json` 覆盖（recall_judge.py:152-159）；经历整理 `[memory.experience].enabled` 被 `data/peter/experience-memory.json` 覆盖（experience_memory.py:133-140）。管理中心若直接展示 character.toml 值，会与实际行为不符。
2. **两个不同的 4000 字节默认**：`veil_agent.py:1074` 写 `positive_int(profile.get('max_bytes', 3000))`，`learned_context.py:39` 写 `DEFAULT_MAX_BYTES = 4000`。实际以 cfg.profile['max_bytes'] 为准（learned_context.py:58），缺省时是 3000。
3. **dimensions 是死键**：仅 `brain_retrieval.py:103` `if dim == 1024`，无人读 memory['dimensions']；改它不生效也不报错。
4. **random_chance 只解析不生效**：`recall_rotation.py:29,75-78` 读入并存进 settings，但从不作概率；唯一作用 `natural_association.py:47-50` 的 `== 0` 兼容关闭。
5. **退役残留**：mode（只允许 native，veil_agent.py:947-950）、ombre_port（:1062 pop）、ombre_model（memory_agent.py:57-58+362 仅假后端）、retrieval.backend（:185-192 强制 brain）。
6. **[memory.events] 有 4 个未文档化键**：evidence_items、evidence_chars、search_query_chars、investigate_rows（event_memory.py:45-46,50-51）。按注释生成表单会漏。
7. **[memory.recall] 有 3 个未文档化键**：directory、directory_members、stage_slots（recall_rotation.py:31-33），默认 6/2 已在生效。
8. **[memory.brain] write_authority 未文档化**：生产恒为真（memory_agent.py:118-119），却像"关闭 Brain 写入"的开关。
9. **校验强度不一致**：experience 未知键→启动失败；events 未知键→静默丢弃；recall 越界→静默回默认（recall_rotation.py:62-88）；同一份 [memory.recall] 又被 natural_association.py:41-60 再解析一遍，规则不同。
10. **页面编辑与 Peter 自写长度不一致**：页面 content 限 1500 字（memory_native.py:25,592-596），Peter hold 上限 `max(model.tools)×4` 字符（:292-306，可为 0=不限）。
11. **stage_slots 与"阶段不封顶"并存**：recall_rotation.py:33 限名额，memory_agent.py:1625-1627 注释"the stage count is no longer capped"，两条规则容易被理解混。
12. **经验记忆与事件记忆共享同一日额度**：daily_budget_cny 同时传给 EventMemory（experience_memory.py:128,177），事件整理花的钱计入同一类别（`dream_pass` 也在启动/整理时跑，memory_native.py:283-288）。"每天 ¥1"实际是两者合计。
13. **自然联想优先级高于普通召回**：联想条 priority 900（memory_agent.py:1799），普通召回 10+index；预算不足时**联想最后被淘汰**——若想"省 token 先砍联想"，当前顺序相反。
14. **enabled 与 mode 职责交叉**：veil_agent.py:1008-1015 的默认工具集判定看的是 mode 而非 enabled，enabled=false 时仍会生成"files off"的工具配置。
15. **relationship_base 层级陷阱**：只有 [profile] 表内的 relationship_base 子表被读（veil_agent.py:1082-1085）；写成顶层 [relationship_base] 会被静默忽略。
16. **误导性 Ombre 文案**：`/api/memory/panel` 失败仍返回「Ombre 记忆暂时无法读取。」（web_agent.py:2078），catalog 失败返回「Ombre memory catalog is temporarily unavailable.」（:2112），P3-2 后已无 Ombre。

---

## 4. 页面与入口现状

### 4.1 已有页面/接口（可页面改）
| 能力 | 页面位置 | 接口 | 实现 | 可改范围 |
|---|---|---|---|---|
| 经历整理开关 | 记忆页 `#experience-row`（chat.html:131） | `POST /api/experience-memory`（web_agent.py:3431-3432） | :3151-3163 → set_enabled | 只有开/关（文案 chat.js:937-940） |
| 想起判官开关 | 记忆页 `#judge-row`（chat.html:132） | `POST /api/recall-judge`（:3433-3434） | :3165-3177 | 只有开/关；熔断只读（chat.js:959-961） |
| 记忆浏览/搜索/分组 | 记忆页 `#memory-view`（chat.html:126-133） | `GET /api/memory/panel|catalog|read|events|event` | _get_memory_panel / _get_memory | 分组：全部/最近/经历/已归档（chat.js:17）；"最近"=30 天（chat.js:763-767）；一次 50 条（chat.js:15,876） |
| 记忆编辑/删除/历史/恢复/归档/解锁/已读 | 记忆详情 | `POST /api/memory/*`（:2545-2597） | NativeMemoryTools.panel_* | 内容与生命周期，不改配置 |
| 经历拆分/阶段移动 | 经历分组 | `POST /api/memory/event/split|/event/move` | split_event / move_stage | 内容层 |
| Learned Context 四张卡 | Peter 页 | `GET/POST /api/peter-page`（:2448,3445） | save_learned / save_ongoing / save_relationship | 文本与条目；显示"已用 / 上限字节"（peter-page.js:310-311） |
| 用户人设 lan.md | Peter 页 | action=save_user_persona（:640-644） | UserPersona.save | 全文，超 max_bytes 报错 |
| 记忆提示词 | 说明页 memory 分类 | `POST /api/instructions`（:3439-3440） | instructions.save（:101-108） | 文本，覆盖写到 persona/instructions/ |
| 记忆只读诊断 | 诊断页 | /api/diagnostics（recall 段） | diagnostics.py:923-928 | 只读 |

### 4.2 只能改文件（无页面）
- [memory] 全部键；[memory.recall] 全部键（含 3 个未文档化）；[memory.recall_judge] 除 enabled 外 5 键；[memory.recall_context] 全表；[memory.experience] 除 enabled 外 8 键（含日额度）；[memory.events] 全部键（含 4 个未文档化）；[memory.retrieval] 两权重；[profile] file/learned/legacy_file/max_bytes、[profile.relationship_base]、[user_persona] max_bytes；[short_term_notes] enabled。
- .env 的 `EMBED_API_KEY`（缺失→语义召回静默降为纯关键词）。

### 4.3 代码写死，无入口
- 判官 MAX_OUTPUT_TOKENS=200 / QUERY_CHARS=600 / CARD_CHARS=300 / SKIPPED_TYPES / 30 条向量扫描上限。
- 置顶 8、目录 5 条/页、合并 48KB、memory_read 1000 字符/页、搜索结果 20。
- 单条记忆长度公式（max(tokens)×4）、页面编辑 1500 字上限。
- 经历整理：5 分钟 tick、每次最多写 2 条/更新 3 条、12000 字/请求、1500 字/条、8000 输出 token、失败退避 30 分/2 小时/次日、12 候选、4 次库检索、6 条命中、6000 字候选上限、3 天回填。
- 事件：归组置信度 0.6（可配的只有窗口/条数/字符/分批/分页）。
- 召回：SEARCH_SPARE_HITS=5、importance 加成 0.05 上限、drift 随机 1 条（<3 结果）、token 校准 0.15..1.0、最近一轮保护地板。
- 冷却历史 400、诊断保留 200、联想丢弃清单 40、经历链 12。
- 短时便签全部内部上限、背景资料 LIMITS、recent_experiences 5 条/18000 字节。

---

## 5. 生效时机与持久化

| 配置类别 | 生效时机 | 依据 | 重启后 | 更新(update-main)后 | 备份恢复后 |
|---|---|---|---|---|---|
| character.toml 全部 [memory*]/[profile*]/[user_persona]/[short_term_notes] | **重启** | veil_agent.py:1059,2379-2383；memory_agent.py:181-192 | 保留 | **风险：文件在仓库内 characters/peter/，git 更新可能覆盖手改**（见无法确认项） | 取决于备份是否含 characters/ |
| 记忆页两个开关 | **立即** | recall_judge.py:151-159；experience_memory.py:133-145 | 保留（data/） | 保留（data/ 不在仓库） | 只恢复 brain.db 则回落到配置默认值 |
| Learned Context / lan.md / 说明页文本 | **下一轮请求** | learned_context.py:298-318；user_persona.py:27-29 | 保留 | 保留（persona/ 是本地覆盖目录） | 需 persona/ 与 data/ 一起备份 |
| 记忆内容 brain.db | 立即写，下一轮可召回 | brain_writer 事务 + brain_authority | 保留 | 保留 | 可 brain_backup.restore，恢复前校验 digest |
| 冷却/轮换状态 | 每轮累加 | memory_agent.py:193-198,1937-1941 | 保留 | 保留 | 丢失只让冷却重置（冒得更早），不影响内容 |
| 费用额度 | 重启读额度；当日已花读 costs.jsonl | experience_memory.py:344-346 | 额度与已花都保留 | 保留 | costs.jsonl 被回滚会少算已花（**风险**） |
| 写入冻结 | 立即 | brain_authority.py:52-53 | 文件在则一直冻结 | 保留 | 备份不含则解冻 |
| 判官熔断 | 内存计时不落盘 | recall_judge.py:140-141,168-169 | **清零** | 清零 | 清零 |
| 一次性脚本 | 手动执行 | 各脚本 main | — | — | 各自隔离/备份 |

---

## 6. 风险与建议分类

**P（底层强制，不给用户改）**
1. 记忆条数/长度/预算硬上限（_memory_limit、recall_budget、日额度）应由程序强制，页面只显示。
2. 写入冻结标记 brain-write-freeze、备份恢复校验（quick_check+digest）、离线修复"先备份后改"是数据安全底线。
3. 删除确认、可改写白名单（只有被展示过的记忆）、可见性过滤（archived/letter/plan/feel）必须留在程序侧。

**S（敏感，需授权/说明影响）**
1. memory.enabled 关闭=他不再有长期记忆（自述随之变化，veil_agent.py:1913-1916）。
2. input_cny_per_million、experience.daily_budget_cny 直接决定花钱，且日额度是"整理+事件"合计。
3. EMBED_API_KEY 与 base_url：换端点意味着查询文本离开本机。
4. [profile] file/learned/legacy_file 决定读写哪份关系资料，改错=失忆或迁移错文件。
5. brain_backup.restore / data_repair repair / simplify_memory --apply 不可逆（有备份也需人确认）。

**A（高级，需说明影响）**
- 召回冷却/去重阈值/摘要截断/判官超时与候选数/事件归组窗口/故事分批。
- max_bytes（profile 与 user_persona）、recent_days/recent_chars、联想三参数。

**U（用户常用，建议优先上页面）**
- 记忆总开关、经历整理开关、判官开关、四个召回开关（cooldown/dedup/excerpt/联想）、置顶上限（现写死 8，建议可调或至少可见）、"最近"分组天数（写死 30 天）、日额度（只读显示 + 受控修改）。

**R（只读运行信息）**
- 本轮召回（候选/隐藏原因/判官选择/预算分配/丢弃原因，已有 recall-diagnostics.jsonl 与诊断页 recall 段）。
- 判官熔断状态、今天花费、待整理字符数、失败次数、下次重试时间（experience.status() 已有）。
- 记忆条数/置顶数/事件数/未完成事件、brain.db 大小、最后备份时间。

**X（冗余/废弃，清理依据）**
- mode（唯一合法值）、ombre_port、ombre_model、retrieval.backend、dimensions、random_chance 的概率语义。
- [memory.recall_context] 整表可视为"未标定实验功能"，默认关且缺 min_similarity 即整表忽略。
- 记忆页/接口的 Ombre 文案（web_agent.py:2078,2112）。

---

## 7. 无法确认项（原因）

1. **所有运行时当前值**：data/peter/experience-memory.json、recall-judge.json、learned-context.json、lan.md、recall-rotation.jsonl、brain.db、costs.jsonl 均不在快照，且只读边界禁止读正式运行目录 → 无法给"当前实际生效值"。
2. **.env 是否配了 EMBED_API_KEY**：无法读取；只能确认缺失时静默降为纯关键词（memory_agent.py:1116-1146）。
3. **characters/peter/character.toml 是否会被 update-main.cmd 覆盖/冲突**：文件在仓库内受 git 管理，但更新脚本逻辑本轮未取证。
4. **brain.db 实际向量维度是否与 dimensions 一致**：代码只接受 1024（brain_retrieval.py:103），无法读真实库校验；若历史库是别的维度，该条向量会被静默跳过。
5. **判官/整理/事件实际用哪个模型与哪套价目**：代码用 `background_model or model`（veil_agent.py:6915,3759）与 model（experience_memory），background_provider 的实际取值需读 models.json/其他配置段，本轮未取证。
6. **线上 [memory.events]/[memory.experience] 的真实开关状态**：以快照 character.toml 为准（events 无 enabled=false → 代码默认 true；experience enabled=true），但正式运行目录可能与快照不同。
7. **P3-3 可见性迁移实际结果**：unhide_legacy_rows（memory_agent.py:133-136）的结果需读 brain.db。
8. **prompts/store.py 的保存上限**：本轮读取该文件时命令解析失败，是否存在单条文本长度限制未确认 → 不敢断言说明页可任意长度。
9. **scripts/p2b_cutover.py 完整割接流程**：只确认调用 brain_backup.backup（:56），其余参数与是否自动建 freeze 文件未取证。
10. **tag_governance 的 category 是否被检索/页面使用**：注释称 "Free tags stop being a UI/retrieval input."，但在 brain_retrieval/brain_read/memory_agent/chat.js 均未搜到读取 → 倾向"仅落库"，未找到反证，标待确认。
