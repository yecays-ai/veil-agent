# 域 4 取证：模型与上下文管理（VEIL main e7e7992 快照）

> 取证范围：只读 main 快照（= 最新 main e7e7992 快照）。
> 下文所有 file:line 均相对该快照根；**未读取正式运行目录 veil-agent**，也未读取任何 data/ 与 .env 实际内容。
> 因此凡是「当前实际值」一律写「无法读取（只读边界：正式运行目录禁读，快照不含 data/ 与 .env）」。

---

## 1. 执行链

### 1.1 聊天模型：三层来源 → 一次请求

1. **读角色配置**：veil_agent.py:832-838 load_character 读 characters/<id>/character.toml（tomllib）。
2. **取 [providers.*] 内置模型**：veil_agent.py:849-887
   - provider =（character.toml:3）指定默认表 → toml_provider() 校验（veil_agent.py:761-779）。
   - 默认表 kind=="claude_agent" 或整表缺失 → 取第一个能解析通过的 provider（855-870）；一个都不存在则 ValueError（872）。
   - 所有能解析的 provider 都成为 builtins（880-887），坏表被静默跳过（886-887）。
   - 一个可用 provider 都没有 → MISSING_PROVIDER 占位（873-878，常量 784-789）。
3. **读本机模型清单**：model_settings.py:110-126 ModelSettings.__init__ → data/<id>/models.json（路径 113）；_read（335-344）→ _migrate_retired_cc_choices（197-230）→ _clean（346-384）。
4. **选出 peter 用的 provider（启动时）**：veil_agent.py:890-895：models.startup_choice('peter')（model_settings.py:546-557）→ chosen('peter')（521-544）→ _provider()（451-477）。选择顺序：use.peter → 缺省 builtin:<default_name>（model_settings.py:117、518）。
5. **真正使用点（每个请求）**：veil_agent.py:6429-6514 make_model。call() 先 models.chosen('peter') 做有效性闸门（6465-6471），实际 provider 取 cfg.provider（6437-6438）；请求体在 6494-6498 组装，max_tokens = max_tokens or cfg.max_output_tokens（6495）。
6. **key 读取**：make_model.client_for（6440-6453）每次只从 <root>/.env 读 p['api_key_env']，无环境变量兜底、无插值（6443-6447）；缺 key → RequestNotSent('本机 .env 缺少所选接口的 API Key，请按 README 填写。')（6447）。
7. **失败行为**：
   - 启动期无可用模型 → 占位 provider，页面提示（model_settings.py:605-609：'还没有可用的聊天模型；请在模型设置里添加接口和模型。' / '当前聊天模型缺少 API Key；请填写 Key 或在模型设置里重新选择。'）。
   - 运行期 chosen('peter') provider 为空 → SettingsError('还没有可用的聊天模型；请先在模型设置里选择。')（model_settings.py:535-536）。
   - 请求异常 → _request_failed（veil_agent.py:3889-3928）：**不自动重试**（3927），写 data/<id>/provider-errors.jsonl（3915-3920）。
   - 预留未结算：budget.settle(..., tolerate_over_bound=True)（4658-4659）；厂商上报超边界时只加标记（1791-1794），不中止。

### 1.2 记忆后台模型（background）

veil_agent.py:896-898 启动时取一次 → Character.background_provider（字段 713）→ 6901 独立建 client → background_cfg()（6575-6578）给 short_term_notes（2374-2375）与想起判官（6915）。
缺省/非法：background_choice is None → MISSING_PROVIDER（897）；chosen('background') 无效 → SettingsError('还没有可用的记忆后台模型；请先在模型设置里选择。')（537-538）。
**结论：改记忆后台模型只在重启后生效**（与页面提示 chat.js:2805 一致），因为 provider 是启动时抓取的 dict。

### 1.3 自主活动 / 瞄一眼 / 委派

- 自主活动：veil_agent.py:3996-3997 每次 send 现取 _activity_model('autonomy')（3979-3990）；与聊天同模型时返回 None（3986-3987），走聊天 provider。
- 瞄一眼：veil_agent.py:3996 取 glance；autonomy.py:1658-1664 置 glance_mode 后 chat.send('Take a brief look at what Lan is doing now.', autonomy=True, extra_context=context)。
- 委派：工具只在「已选委派模型 + 工具模式 + 非自主 + 非瞄一眼」时出现（veil_agent.py:2456-2466）；执行 _run_delegate（5202-5238）chosen('delegate')，max_output_tokens=min(cfg.max_output_tokens, 1200)（5216-5217），timeout=45（5219）。未选 → SettingsError('请先在模型设置里选择"委派任务"使用的模型。')（model_settings.py:529-530）。
- 输入窗口：veil_agent.py:4015-4026 _send_with_input_limit：limit = autonomy.max_input_tokens（4016）；仅 role=='autonomy' 时才 min(..., provider.max_input_tokens)（4008-4009）；随后**直接替换 provider 的 max_input_tokens**（4021-4022）。

### 1.4 上下文窗口与裁剪（每次请求）

1. **取历史**：veil_agent.py:3431-3505 recent()；窗口轮数 history_keep(total_groups, cfg.history_rounds)（3462），算法 1952-1959（chunk = limit//4，一次丢一批以保住前缀缓存）。丢多少记 history_window（3468-3470）。
2. **图片降级**：只有最新一张用户照片保留真实图片（3474-3497），历史照片一律 PHOTO_PLACEHOLDER（71、2121-2139、4357）。
3. **拼 head**：veil_agent.py:4177-4218（人设 → user_persona → voice → learned profile → relationship_base → system.facts → 经历笔记）。
4. **预算上限**：limit = cfg.provider['max_input_tokens']（4309）。
5. **记忆/历史/辅助按比例分**：4370-4407（recall_budget.split，recall_budget.py:88-123），随后安全循环从旧到新丢历史（4410-4418）。
6. **仍超限 → 明确报错**：veil_agent.py:4435-4450，文案原文：
   '当前输入及必要上下文太长，超过模型上限（上限 {limit} tokens，必要估算≈{total_est} tokens）：…。已裁剪全部历史与记忆，仍无法容纳；请更换更大输入上限的模型或减少启用的工具。'
7. **裁剪说明**：4455-4467 写 trim_note（'（长度估算：本轮只带最近 {kept} 轮原文；分项 tokens：…；完整聊天仍保存在本机。）'），进快照/context-log。
8. **工具循环内的二次裁剪**：veil_agent.py:5560-5570（工具结果截断 → '结果过长'）、5659-5669（预检超限 → TooLong('工具说明和本轮上下文超过长度上限，未调用工具；请缩短输入后再试。')）。

### 1.5 指令（提示词）

1. 登记表：prompts/registry.py:66-333（72 条 text + 27 条 marker；其中 28 条 text 为 editable=False）。
2. 取文：prompts/__init__.py:49-51 text() → prompts/store.py:87-96：**persona/instructions/<id>.md 非空则整条覆盖，否则用 prompts/defaults/<id>.md**；覆盖文件损坏也不炸（92-95）。
3. 保存：web_agent.py:573-584 save_instruction → instructions.py:101-108 → prompts/store.py:121-159 save；字节上限 min(entry.max_bytes, 40000)（130-132），占位符合规校验（133-137）。
4. 工具说明覆盖：prompts/tools.py:57-88（persona/instructions/tool.<name>.md，上限 4000，17），注入点 veil_agent.py:2520-2530。
5. 唯一「版本」痕迹是默认文本指纹 .base.json（prompts/store.py:19、32-64、98-119），只用于显示「默认已更新」；**没有历史版本、没有一键恢复默认**。

### 1.6 快照与观测

1. 每步 veil_agent.py:4543-4556 snapshot_step → request_snapshots.build/save（129-204）；采集失败不影响请求（4553-4554、203-204）。
2. 容量：KEEP_REQUESTS=40、BLOCK_CHARS=12000、TOTAL_CHARS=300000、TOOLS_CHARS=120000（request_snapshots.py:20-23）；裁剪 141-147；按 request_id 分组保留 40 组（211-229）。
3. 脱敏：SECRET 正则（26-34）→ '[已隐藏]'（99-106）。
4. 读接口：web_agent.py:2888-2931（列表 limit 1..40，2896-2897；详情支持 message_id 反查，2910-2923）。
5. 用量校准：usage_status_for（veil_agent.py:2009-2032）+ Budget.input_token_ratio（1730-1748）+ conservative_token_ratio（2003-2006）。

---

## 2. 配置清单表

> 存储位置缩写：toml=characters/peter/character.toml；models.json=data/<角色>/models.json；env=.env；code=代码常量。分类码：U/A/S/R/P/X。

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 内置模型 | provider（默认 provider 名） | veil_agent.py:852-872 | toml:3 | 无（缺表时取第一个可解析项） | 必须是 [providers.*] 已存在的键 | 仅改文件 | 决定 builtins 默认项；models.json 的 use.peter 仍可覆盖 | 重启 | 文件（受 git 跟踪） | 写错→启动 ValueError；claude_agent 被跳过 | U |
| 内置模型 | [providers.X].model | veil_agent.py:763-765 | toml:13,25 | 必填 | 非空字符串（照中转站抄） | 仅改文件 | 被 use.* 选中后才用 | 重启 | 文件 | 名称错→厂商 404，页面只显示失败 | A |
| 内置模型 | base_url | veil_agent.py:766-767 | toml:15,28 | 必填 | 必须 https:// 开头 | 仅改文件 | — | 重启 | 文件 | http 直接启动失败 | A |
| 内置模型 | api_key_env | veil_agent.py:763-765；6444-6447 | toml:16,29 | 必填 | .env 里的变量名 | 仅改文件（页面只管 models.json 的接口 key） | 只从 .env 读，无环境兜底 | 重启（client 缓存 key+url） | .env（git-ignored） | 缺 key→RequestNotSent，不扣费 | S |
| 内置模型 | max_input_tokens | veil_agent.py:772；4309；5562 | toml:17(100000),30(32768) | 必填（无默认） | 正整数，**无上限校验** | 仅改文件 | 裁剪硬上限；可被 autonomy.max_input_tokens 反向覆盖（见 §3） | 重启 | 文件 | 写太大→厂商报错才算失败 | A |
| 内置模型 | thinking | veil_agent.py:6490-6491 | toml:14="enabled"；gpt 未写 | 无默认（缺省=不发参数） | 无校验；只有字面 'disabled' 有作用 | 仅改文件 | 覆盖 models.json 同名字段（该 role 用它时） | 重启 | 文件 | 写 disable/off 静默失效 | X |
| 内置模型 | input_cny_per_million / cached_input_cny_per_million / output_cny_per_million | veil_agent.py:768-771；1774-1777 | toml:20-22,32-34 | 必填 | >0 有限数；cached ≤ input；CNY/百万 token | 仅改文件 | 覆盖 models.json 价格 | 重启 | 文件 | 价格错→预算/结算失真（无对账） | A |
| 内置模型 | input_modalities | veil_agent.py:773-778 | toml:27（仅 gpt） | 无（=纯文本） | ['text'] 或 ['text','image'] | 仅改文件 | 决定能否发图片 | 重启 | 文件 | 无 image 却发图→厂商拒绝后降级（5734-5796） | A |
| 内置模型 | kind="claude_agent" | veil_agent.py:855-863,882-883 | toml（当前无） | 无 | 只被识别为「要跳过」 | 仅改文件 | 被跳过的表不进 builtins | 重启 | 文件 | Ticket 317 遗留；留着只是不生效 | X |
| 内置模型 | cny_per_call（按次计费） | veil_agent.py:1539,1774 | toml（未使用） | 无 | 需同时写三个价格才能通过解析 | 仅改文件 | 有它则 reserve/settle 改按次 | 重启 | 文件 | 文档未写；写错=多收/少收 | X |
| 本机模型 | endpoints[].id/name/base_url | model_settings.py:386-394 | models.json | 无（自建） | id e[0-9a-f]{6}；name ≤40；https | 页面「设置→模型→接口」（chat.html:202-208） | 覆盖内置 provider（用 use.* 选中时） | 下一次请求 | 文件 0600（283-296） | url 校验 _base_url（63-67） | U |
| 本机模型 | endpoints[].key_env → VEIL_KEY_<ID> | model_settings.py:394；586-589 | models.json + .env | 自动生成 | 页面只写 .env，不回显（key_hint 只给后 4 位） | 页面填 key（chat.html:205） | — | 下一次请求 | .env（git-ignored，0600） | _key 禁空格/引号（70-74）；不落 models.json | S |
| 本机模型 | 接口数量上限 MAX_ENDPOINTS | model_settings.py:32,622-624 | code | 20 | 0..20 | 代码写死，无入口 | — | — | — | 超限报 '接口或模型太多了。' | P |
| 本机模型 | models[].model / label | model_settings.py:406-408 | models.json | label 缺省 '' | model ≤200；label ≤40 | 页面「添加模型」（chat.html:213-214） | — | 下一次请求 | 文件 | label 参与瞄一眼 grok 匹配（496-497） | U |
| 本机模型 | models[].thinking | model_settings.py:408,463；veil_agent.py:6490 | models.json | True | bool | 页面复选框「思考模式（DeepSeek 必开）」（chat.html:221） | 覆盖内置默认 | 下一次请求 | 文件 | 关掉思考=只发 extra_body.thinking.disabled | A |
| 本机模型 | models[].billing | model_settings.py:403-405 | models.json | 必填 | 'call' / 'tokens' | 页面「按次 / 按 token」（chat.html:215） | 决定用哪组价格 | 下一次请求 | 文件 | 改 billing 少填价格→保存报错 | U |
| 本机模型 | cny_per_call | model_settings.py:409-410 | models.json | 无（按次必填） | 0<x≤100 CNY/次 | 页面「每次 ¥」（chat.html:216） | 按次时其余三价强制 0（470-473） | 下一次请求 | 文件 | 与 tokens 价互斥，切换会清价 | U |
| 本机模型 | input_cny_per_million / output_cny_per_million | model_settings.py:411-415 | models.json | 无（按 token 必填） | 0<x≤10000 CNY/百万 | 页面（chat.html:217,219） | — | 下一次请求 | 文件 | 无价格校验对账，只影响预算 | U |
| 本机模型 | cached_input_cny_per_million | model_settings.py:416-421 | models.json | = input 价 | 0..input 价 | 页面留空=同输入价（chat.js:2886） | 覆盖默认 | 下一次请求 | 文件 | >input 直接报错 | U |
| 本机模型 | max_input_tokens（API 模型） | model_settings.py:422-429 | models.json | DEFAULT_MAX_INPUT=32768（31） | 4096..2000000 tokens（'最长输入要在 4096 到 2000000 tokens 之间。'） | 页面「最长输入」（chat.html:220） | 覆盖该 provider 的窗口 | 下一次请求 | 文件 | 与内置 provider 的「无上限」不一致（772） | A |
| 本机模型 | reasoning_field | model_settings.py:430-434；veil_agent.py:2064-2072 | models.json | 无 → reasoning_content | 'reasoning_content' / 'reasoning' | **仅改文件；页面保存会被丢掉**（chat.js:2821 不含该键，save 无 carry-forward） | 覆盖默认字段 | 下一次请求 | 文件 | 页面一保存即丢失 → 推理内容不再回传 | X |
| 本机模型 | input_modalities（API 模型） | model_settings.py:435-440；656-658 | models.json | 无（纯文本） | 必须含 text，只允许 text/image | 页面不能设；只有 endpoint+model 都没改时才保留（656-658） | — | 下一次请求 | 文件 | 改名即丢；丢后图片只能靠报错降级 | X |
| 本机模型 | 模型数量上限 MAX_MODELS | model_settings.py:33,622-624 | code | 60 | 0..60 | 代码写死，无入口 | — | — | — | 超限同上 | P |
| 预算 | limits.daily_cny | model_settings.py:443-449,567-572；veil_agent.py:6894 | models.json | DEFAULT_DAILY=10（29） | 0<x≤1000（'不能超过 1000。'） | 页面「每天 ¥」（chat.html:199，min=1 max=1000） | 覆盖默认；验收模式还会 min（web_agent.py:1526-1528） | 立即（web_agent.py:1529） | 文件 | 硬闸：Budget.reserve（1552-1553） | U |
| 预算 | limits.monthly_cny | 同上 | models.json | DEFAULT_MONTHLY=300（30） | 0<x≤30000，且 ≥daily（448） | 页面「每月 ¥」（chat.html:199） | 同上 | 立即 | 文件 | 同上 | U |
| 角色映射 | use.peter | model_settings.py:518；veil_agent.py:890-895；web_agent.py:1541-1548 | models.json | builtin:<provider> | 任一可用模型 id | 页面「Peter 本人」/顶栏（chat.html:189；chat.js:2851-2852） | 最高（覆盖 character.toml 默认） | **下一句**（chat.js:190、2847） | 文件 | 切到无 key 模型→下一请求报错 | U |
| 角色映射 | use.autonomy | model_settings.py:504-517,561；veil_agent.py:3996-3997 | models.json | 缺省=跟随聊天 | 模型 id 或 'follow-chat' | 页面「自主活动」（chat.html:191，'留空跟随聊天模型'） | 缺省跟随 peter | 下次自主活动（chat.js:2845） | 文件 | 页面显示 follow-chat 时实际可能取到别的模型（561 vs 504-517） | U |
| 角色映射 | use.background | model_settings.py:518；veil_agent.py:896-898,6901 | models.json | 缺省=内置默认 | 模型 id | 页面「记忆后台」（chat.html:198 '重启后生效'） | 覆盖缺省 | **重启**（2805） | 文件 | 价格同理只在重启后生效 | U |
| 角色映射 | use.glance | model_settings.py:490-503；autonomy.py:1658-1664 | models.json | 缺省：命中 grok-4.6 则用它，否则 follow-chat | 模型 id 或 'follow-chat' | 页面「瞄一眼」（chat.html:195 '只用于瞄一眼'） | 覆盖缺省；GLANCE_DEFAULT_MODEL 只兜底 | 下次瞄一眼（chat.js:2844） | 文件 | 未显式选过也会显示 grok | U |
| 角色映射 | use.delegate | model_settings.py:488-489,529-530；veil_agent.py:2456-2466 | models.json | None=关闭 | 模型 id 或空 | 页面「委派任务」（chat.html:193-194 '留空关闭'） | 未选则工具不出现 | 下次请求（工具表重建） | 文件 | 关闭时模型完全拿不到委派能力 | U |
| 角色映射 | ROLES / ROLE_LABELS | model_settings.py:21,26-27 | code | ('peter','autonomy','background','glance','delegate') | 固定 5 个 | 代码写死，无入口 | — | — | — | 新增角色要改代码 | P |
| 角色映射 | FOLLOW_CHAT='follow-chat' | model_settings.py:22,527-528,716-723 | code | 字面量 | 仅 glance/autonomy 接受 | 代码写死 | 递归取 chosen('peter') | — | — | 存进 use.autonomy 会被当「无显式选择」删除（716-723） | P |
| 角色映射 | GLANCE_DEFAULT_MODEL='grok-4.6' | model_settings.py:28,494-503 | code | 'grok-4.6' | 子串匹配 label/model | **代码写死，无入口** | 只在 glance 无显式选择时兜底 | 下次瞄一眼 | — | 换偏好模型要改代码 | X |
| 角色映射 | RETIRED_CC_BUILTINS + 迁移记录 | model_settings.py:25,146-269 | code + models.json migration | 4 个旧名 | 仅这四个确认名 | 自动迁移一次；无页面 | 一次性替换 peter/background，其余清空 | 启动 | .cc-removal-backup 备份（153-160） | 页面保存会清掉 migration 提示（662-663） | X |
| 上下文 | history_rounds | veil_agent.py:1157；3462；1952-1959 | toml:6（80） | 20 | 正整数（轮） | **仅改文件** | 与 provider 窗口双约束 | 重启 | 文件 | 80 轮常被 token 预算裁到更少（4359-4418） | U |
| 上下文 | max_output_tokens（角色级） | veil_agent.py:1158；6494-6495 | toml:7（1024） | 1024 | 正整数（tokens） | **仅改文件** | 被 tools.max_output_tokens 覆盖（工具模式） | 重启 | 文件 | 过小→回复被截断并触发续接（5884-5914） | A |
| 上下文 | tools.max_output_tokens | veil_agent.py:918；4490-4491；5656 | toml:117（4096） | = cfg.max_output_tokens | 正整数 | **仅改文件** | 覆盖角色级（仅工具步骤） | 重启 | 文件 | 也写进工具说明（2482-2484） | A |
| 上下文 | tools.max_steps | veil_agent.py:916,932-933 | toml:123（20） | 6 | 正整数且 ≥2（'tools.max_steps must allow a tool call and a final response'） | **仅改文件** | 与 autonomy.max_tool_steps 取小（5588-5589） | 重启 | 文件 | 自主活动仅在此之上 +1 步（5593） | A |
| 上下文 | autonomy.max_input_tokens | veil_agent.py:1028-1037,4015-4022 | toml:147（100000） | 无（缺省=provider 窗口） | 正整数 | **仅改文件** | 覆盖 provider 的 max_input_tokens（不是 min） | 重启 | 文件 | 可把 32768 的模型抬到 100000（见 §3） | A |
| 上下文 | autonomy.run_budget_cny / max_tool_steps | veil_agent.py:1029-1041,5586-5588 | toml:149-150 | run_budget 无默认；steps 缺省 60 | 价格>0；steps 正整数 | **仅改文件** | 有 run_budget 才启用步数上限 | 重启 | 文件 | 只给自主活动，不给聊天 | A |
| 上下文 | glance.max_steps | veil_agent.py:1049-1052；autonomy.py:1460；veil_agent.py:5589 | toml:160（4） | 缺省 4，**上限 4** | 1..4 | **仅改文件** | min(tools.max_steps, glance.max_steps) | 重启（controller 构造） | 文件 | 写 5 直接 ValueError（1051-1052） | A |
| 上下文 | glance.min/max_interval_minutes、idle_seconds、daily_budget_cny | veil_agent.py:1046-1055；autonomy.py:1457-1461 | toml:157-161 | 15/40/300/¥1 | 正整数；max≥min；价格>0 | **仅改文件** | — | 重启 | 文件 | 属域 5，此处只列模型调用频率 | A |
| 上下文 | glance.enabled（双源） | veil_agent.py:1045；autonomy.py:1455-1504 | toml:156 + data/<id>/glance-settings.json | toml=true；文件缺省=沿用 toml | bool | 页面 /api/glance 开关写 json（autonomy.py:1497-1504） | **json 覆盖 toml**（1481-1492） | 立即 | 两处都持久 | 两处不一致时以 json 为准，页面不显示 toml 值 | A |
| 上下文 | 估算常量 calibration=0.4、clamp 0.15-1.0、4096/image、MESSAGE_FRAMING_BYTES=16 | veil_agent.py:54-60,1983-2006 | code | 0.4 / 0.15-1.0 / 4096 / 16 | 固定 | **代码写死，无入口** | 校准值可被实测覆盖（2003-2006） | 每请求 | — | 改它就是改「提前报太长」的阈值 | P |
| 上下文 | 校准门槛 MIN_TOKEN_CALIBRATION_BYTES=16384、比率 0.05-1.0、减半判定 | veil_agent.py:55,59-60,2009-2032,1730-1748 | code | 16384 / 0.05 / 1.0 | 固定 | **代码写死，无入口** | 不可信 usage 被丢弃 | 每次结算 | costs.jsonl usage_status | 防中转站谎报 | P |
| 上下文 | history_keep 分块丢弃 | veil_agent.py:1952-1959 | code | limit//4 | 固定 | **代码写死，无入口** | 决定前缀缓存命中 | 每次 recent() | — | 窗口=80 时一次丢 20 轮 | P |
| 上下文 | 记忆/历史/辅助比例分配 | recall_budget.py:88-123；veil_agent.py:4370-4418 | code | 按实测比例 | 固定 | **代码写死，无入口** | 保护最新一轮完整对话 | 每请求 | — | 记忆多时历史被压 | P |
| 上下文 | collapse_latest_photo / collapse_all_history_photos | veil_agent.py:2121-2139,3489-3497,4357 | code | '[Lan sent a photo]'（71） | 固定 | **代码写死，无入口** | 只有最新一张照片是真图 | 每请求 | — | 视觉 token 只在最新图 | P |
| 上下文 | context.work_checkpoint | veil_agent.py:1152-1155,4240-4251；work_checkpoint.py:37 | toml:135（true） | True | bool | **仅改文件**；页面只能「结束这个断点」（chat.html:102） | 关掉则不写不注入 | 下一次聊天 | data/<id>/work-checkpoint.json | 非 bool 启动报错 | A |
| 上下文 | 断点常量 6/4/3/300/48h | work_checkpoint.py:15-19 | code | MAX_CONFIRMED=6 等 | 固定 | **代码写死，无入口** | — | — | 文件（48h 后过期） | — | P |
| 系统提示 | system.facts 模板与占位符 | prompts/defaults/system.facts.md:1-4；veil_agent.py:1929-1940 | 默认文件 + persona/instructions/system.facts.md | 见默认文件 | 占位符 {name}{provider}{model}{channels}{rounds}{memory}{tools}{voice}{capabilities}{maintenance} | 指令页「系统事实（身份、模型、能力）」 | 用户覆盖整条优先 | 下一请求 | persona/instructions（git-ignored） | 删占位符会被拒（store.py:133-137） | U |
| 系统提示 | 变化段（时间+账本） | veil_agent.py:1941-1943,4173 | code | 固定 | 固定 | **代码写死，无入口** | 追加在 system.facts 后 | 每请求 | — | 属只读运行信息 | R |
| 系统提示 | persona（无字节上限） | veil_agent.py:839-847 | persona/peter.md | 必填非空 | 任意文本 | Peter 页 | 最先（head[0]） | 下一请求 | 文件 | 空→拒绝启动 | U |
| 系统提示 | profile.max_bytes / user_persona.max_bytes | veil_agent.py:1074,1092-1093 | toml:169(4000),186(4000) | 3000（profile）/4000（user_persona） | 正整数（字节） | Peter 页（peter-page.js:23 显示 字符·字节/token） | — | 下一请求 | 文件 | 超限被截/拒绝 | U |
| 指令 | 指令条目（72 text + 27 marker） | prompts/registry.py:15-24,66-333 | 默认 prompts/defaults/<id>.md；覆盖 persona/instructions/<id>.md | 见 defaults | 每条 max_bytes（600-30000，registry 内逐条） | 指令页（chat.html:108-125）逐条编辑 | 覆盖整条生效（store.py:87-96） | 下一请求 / 下次自主（chat.js:2324,2352） | 覆盖文件 git-ignored（.gitignore:37） | 28 条 editable=False 只读（安全/协议文本） | U |
| 指令 | 单条字节上限 | prompts/store.py:18,130-132 | code | MAX_BYTES=40000 | min(entry.max_bytes, 40000) 字节 | 保存时校验 | 覆盖逐条上限 | 保存时 | — | 超限报 '…字节' | U |
| 指令 | 占位符必须保留 | prompts/store.py:133-137 | code | — | 该条声明的 {name} 全部保留 | 保存时校验 | 覆盖 | 保存时 | — | 删了会被拒并解释后果 | P |
| 指令 | 工具说明覆盖 tool.<name>.md | prompts/tools.py:16-17,57-88；veil_agent.py:2520-2530 | persona/instructions/tool.*.md | 用代码内 schema 描述 | 名字 [A-Za-z0-9_]≤80；≤4000 字节 | 指令页「工具」区（instructions.py:36-73） | 覆盖 schema 描述 | 下一请求 | 文件 | 空说明被拒 | U |
| 指令 | 默认文本指纹 .base.json | prompts/store.py:19,32-64,98-119 | persona/instructions/.base.json | 自动 | sha1 | 无入口（只读提示） | — | 保存时 | 文件 | '默认已更新' 只是提示，不自动改 | R |
| 指令 | 恢复默认 / 版本回滚 | prompts/store.py 全文（无删除方法）；chat.js:2335-2359（只有保存/取消） | — | **不存在** | — | **无页面入口；只能手动删 persona/instructions/<id>.md** | — | 删除后下一请求 | 删除即回到默认 | 误删无法从页面找回 | X |
| 推理 | thinking 请求参数 | veil_agent.py:6490-6491,6463-6464 | code | 不发（=厂商默认） | 只有字符串 'disabled' 触发 extra_body={'thinking':{'type':'disabled'}} | 页面只能给 API 模型勾选（存 models.json） | 参数级覆盖 provider 值 | 下一请求 | — | 无「强制开」参数 | P |
| 推理 | reasoning_content/reasoning 回传 | veil_agent.py:2064-2096,5845-5849 | code | reasoning_content | 二选一（按 provider 声明） | 仅 models.json reasoning_field | 覆盖默认字段 | 每请求 | — | 只回传该模型声明的那个字段（2075-2087） | P |
| 重试 | 客户端不重试 max_retries=0 | veil_agent.py:6451-6452；3927 | code | 0 | 固定 | **代码写死，无入口** | — | — | — | 失败不自动重试，只记 provider-errors | P |
| 重试 | 超时 MODEL_TIMEOUT_SECONDS=180；委派 45s/90s | veil_agent.py:6426；5219；delegate_agent.py:46,65 | code | 180 / 45 / 90 秒 | 固定 | **代码写死，无入口** | 单请求超时 | — | — | 慢中转站会直接判超时 | P |
| 重试 | 空回复重试 1 次 / length 续接 1 次 / 工具输出超限恢复 1 次 / 假表情包重试 2 次 | veil_agent.py:5951-5975；5884-5914；5850-5880；5930 | code | 1/1/1/2 | 固定 | **代码写死，无入口** | 只在同一步内 | 下一步 | — | 每次都重新预留费用（5964-5966 等） | P |
| 重试 | 图片被拒降级 | veil_agent.py:3931-3939,5734-5796 | code | 一次降级 | 固定 | **代码写死，无入口** | 只看 400/413/415/422 | 同一步 | — | 降级后明确告诉 Peter 没看见 | P |
| 重试 | MISSING_PROVIDER 占位 | veil_agent.py:784-789,873-878,6441-6442 | code | model '未选择模型'，max_input 4096，价格 0 | 固定 | **代码写死，无入口** | 无模型时唯一 provider | 启动 | — | 不会被列出、不会被当可用模型 | P |
| 重试 | blocked（保存未完成） | model_settings.py:120,298-326,524-525 | code + .env.models-<id>.pending | False | 固定 | 代码写死 | 一旦置位全部模型请求停 | 启动/保存 | 事务日志 | '模型设置保存未完成；已停止模型请求，请重启恢复。' | S |
| 结算 | over_bound 容忍 | veil_agent.py:1770-1772,1791-1800,4658-4659,5825-5826 | code + costs.jsonl | tolerate_over_bound=True | 固定 | **代码写死，无入口** | 记 accounting='…usage-over-bound' + bounds | 每次结算 | costs.jsonl | 不再中止服务（旧分支成死路） | R |
| 结算 | 按次 vs 按 token 计价 | veil_agent.py:1539-1541,1774-1777 | provider 三项价格 | 按 token | 有 cny_per_call 走按次 | 页面 billing 切换 | 覆盖 | 每次预留/结算 | costs.jsonl | 预留用「满窗口」上限（1539-1541），比实际高 | A |
| 观测 | 快照保留 40 / 单块 12000 / 总量 300000 / 工具 120000 | request_snapshots.py:20-24,141-147,211-229 | code + data/<id>/request-snapshots/*.json | 40/12000/300000/120000 | 固定 | **代码写死，无入口** | 超过即截断并入 truncated_blocks | 每请求 | 文件（git-ignored data/） | 记 complete 位，页面不谎称全文 | R |
| 观测 | SECRET 脱敏 | request_snapshots.py:26-34,99-106 | code | sk-/ghp_/Bearer/…→[已隐藏] | 固定 | **代码写死，无入口** | — | 写入时 | 文件 | 正则不覆盖所有 key 形态 | P |
| 观测 | input_bytes / usage_status / 校准 | veil_agent.py:2009-2032,1730-1748,1795-1798,4550-4551 | code + costs.jsonl + context-log | — | 只读 | **代码写死，无入口** | 只用于估算，不改请求 | 每结算 | costs.jsonl | 不可信用量会被忽略 | R |
| 观测 | context-log.jsonl 保留 500 行 | background.py:18,304-306；veil_agent.py:3826-3858 | code + data/<id>/context-log.jsonl | 500 | 固定 | **代码写死，无入口** | — | 每请求 | 文件 | 只记大小与 id，不记正文 | R |
| 观测 | /api/request-snapshots limit | web_agent.py:2896-2898 | 请求参数 | 12 | 1..40 | 页面「指令→请求快照」 | — | 即时 | — | 需 X-Token | R |

---

## 3. 重复 / 冲突 / 隐性覆盖

1. **同一个 peter 模型有三个来源**：character.toml [providers.*]（builtins）+ models.json use.peter + models.json endpoints/models。内置表被装进 builtins 后与 API 模型同列（model_settings.py:591-600），页面把它们混在一个下拉里。
2. **autonomy.max_input_tokens 会抬高 provider 自己的窗口，而不是压低**：veil_agent.py:4015-4022 在 limit != provider['max_input_tokens'] 时**替换** provider 的 max_input_tokens；只有 role=='autonomy' 且另选了模型时才 min()（4008-4009）。现状：deepseek 声明 100000、autonomy 也是 100000，正好相等；一旦聊天模型换成 max_input_tokens=32768 的内置表（character.toml:30 gpt），跟随聊天的自主活动/瞄一眼会被抬到 100000 再发给 32768 的模型。瞄一眼（role='glance'）连 min() 都没有。
3. **页面显示值与实际值可能不一致（自主活动）**：_public_use('autonomy') 在无显式选择时固定返回 FOLLOW_CHAT（model_settings.py:559-561），但 selected('autonomy') 在聊天模型不可用时会回落到 default/builtin/任意 API 模型（504-517）。页面写「跟随聊天模型」，实际请求可能用别的模型。
4. **页面保存会丢 reasoning_field**：chat.js:2821 只回传 11 个键，不含 reasoning_field；model_settings.save 只对 input_modalities 做了「沿用旧值」（656-658），其余按提交重建。任何一次「保存模型」都会把手工写进 models.json 的推理字段抹掉。
5. **input_modalities 只在 endpoint+model 都没改时保留**（model_settings.py:656-658）。改一下模型名，多模态声明就没了，图片只能靠厂商报错走降级（veil_agent.py:5734-5796）。
6. **保存接口不带 limits 会重置成 10/300**：model_settings.py:662-663 用 _limits(payload.get('limits') or {})，缺省即 DEFAULT_DAILY=10 / DEFAULT_MONTHLY=300（29-30），而不是保留当前值。页面每次都会带上（chat.js:2822），但脚本/未来管理中心直接 POST 就会静默改预算。
7. **保存会清掉 Claude 下线迁移提示**：save() 重建的 data 只含 endpoints/models/use/limits（662-663），没有 migration，于是 _migration_notice（244-269）不再显示——提示没被确认就消失。
8. **删模型静默重置角色**：save() 里对每个 role 只在 _choice_ok 通过时写入（665-670），被删掉的模型对应角色直接丢失，无二次确认。页面删除按钮只有「再点一次」（chat.js:2792）。
9. **内置 provider 的 thinking 不校验**：toml_provider 从不检查该键（veil_agent.py:761-779），而使用点只认 'disabled'（6490-6491）。写 'disable'/'off' 等于没写，日志也看不出来。
10. **内置 provider 支持按次计费但没人知道**：toml_provider 强制要求三个 token 价格（768-769），而 reserve/settle 又优先 cny_per_call（1539,1774）。于是「内置按次」必须先写三个假价格再写 cny_per_call，文档/页面都没有这个入口。
11. **死键 [memory] ombre_model**：memory_agent.py:57-58 仍在读，但只在 authority is None 的老分支用（362）；P3-2 之后生产路径必挂 Brain authority（memory_agent.py:112-127），该分支不可达。文档也写明已清理（docs/tickets/p3/P3-2.md:15）。
12. **reasoning_field 非法值静默回落**：veil_agent.py:2071-2072 只接受两个值，其它值一律当 reasoning_content，models.json 手工写错不会有任何报错（对比 model_settings.py:431-432 只在页面保存时报错）。
13. **history_rounds 与实际携带轮数不是一个数**：窗口 80 轮（character.toml:6），但请求里常被 token 预算裁到更少（veil_agent.py:4399-4418），system facts 写的却是 80（1934）。
14. **max_input_tokens 两套校验标准**：models.json 有 4096..2,000,000 的硬范围（model_settings.py:427-428），character.toml 只要求正整数（veil_agent.py:772）。
15. **glance.enabled 两个真源**：character.toml:156 与 data/<id>/glance-settings.json（autonomy.py:1463,1481-1504）。页面开关只写 json，不回写 toml，也不显示 toml 现值。
16. **瞄一眼模型的静态兜底**：没显式选过也会因为 label/model 里含 grok-4.6 而自动选中它（model_settings.py:494-503），页面显示成「已选」，但 Lan 从未点过。
17. **两套观测容量不同**：请求快照保留 40 个 request（request_snapshots.py:20），context-log 保留 500 行（background.py:18）。同一轮在两边都能查到，但「能看到多远」不同；快照默认只列已发送（request_snapshots.py:255-257），预检记录被过滤。
18. **.env 的 key 只被页面管理的接口写**：删除接口会 unset_key（model_settings.py:671-683,749-751），内置 provider 的 key 页面既看不到也改不了（只能改 .env 文本）。

---

## 4. 页面与入口现状

### 4.1 已有页面/接口

| 能力 | 入口 | 证据 |
|---|---|---|
| 模型卡片（5 个角色下拉 + 费用上限） | 设置页 #models-card | desktop/chat.html:186-199 |
| 接口增删（name/url/key） | 同卡「接口」 | chat.html:200-208；chat.js:2858-2873 |
| 模型增改（endpoint/model/label/billing/三价/max_input/thinking） | 同卡「模型」 | chat.html:211-223；chat.js:2880-2902 |
| 顶栏快速切 peter 模型 | #model-switch | chat.js:2796,2802,2851 |
| 费用上限保存 | 「保存」按钮 → POST /api/models | chat.js:2857 |
| 指令清单 + 逐条编辑 | 指令页 | chat.html:108-119；chat.js:2237-2366 |
| 请求快照列表/详情 | 指令页第二个 tab | chat.html:120-124；chat.js:2368-2450 |
| 工作断点查看/结束 | 上下文页 | chat.html:102 |
| 诊断（含每服务 model、模型请求链路） | 诊断页 | chat.html:155-183；diagnostics-page.js:305-330 |
| Peter 页字节上限提示 | Peter 页 | peter-page.js:23 |

接口（全部要 X-Token，且校验 Host/Origin，web_agent.py:1981-1998）：

- POST /api/models、POST /api/models/use（web_agent.py:3425-3426；实现 2818-2834）
- GET /api/instructions、POST /api/instructions（2450-2451、3439-3440）
- GET /api/request-snapshots、GET /api/request-snapshot（2452-2455；实现 2888-2931）
- **模型卡片的数据不是 GET /api/models**，而是轮询 /api/events 里的 models 字段（web_agent.py:1922），首次轮询会 reload=True 重读 models.json（2161-2172）。
- POST /api/glance 只切瞄一眼开关（2805-2816）。

### 4.2 只能改文件（无页面入口）

- characters/peter/character.toml 全部跟模型/上下文有关的键：provider、[providers.*] 全字段、history_rounds、max_output_tokens、[tools] max_output_tokens/max_steps、[autonomy] max_input_tokens/run_budget_cny/max_tool_steps、[glance] 五键、[context] work_checkpoint。
- .env：内置 provider 的 key（页面只写 VEIL_KEY_<接口ID> 这一种）。
- models.json 的 reasoning_field、input_modalities（页面不能设，且保存会丢）。
- 恢复某条指令的默认文本（没有任何删除/恢复接口）。

### 4.3 代码写死，无入口

GLANCE_DEFAULT_MODEL（model_settings.py:28）、DEFAULT_MAX_INPUT/DAILY/MONTHLY（29-31）、MAX_ENDPOINTS/MODELS（32-33）、估算/校准常量（veil_agent.py:54-60）、history_keep 分块（1952-1959）、记忆预算比例（recall_budget.py:88-123）、重试与续接次数（veil_agent.py:5850-5975）、超时（6426；delegate_agent.py:46,65）、快照容量（request_snapshots.py:20-24）、context-log 500 行（background.py:18）、断点常量（work_checkpoint.py:15-19）、指令指纹机制（prompts/store.py:19）。

---

## 5. 生效时机与持久化

| 配置 | 生效时机 | 依据 |
|---|---|---|
| use.peter / 模型价格（peter） | **下一句** | web_agent.py:1531-1532 触发 switch_model；veil_agent.py:3941-3954 |
| use.autonomy / use.glance / use.delegate | 下次对应活动 | chat.js:2844-2846；veil_agent.py:3996-3997 |
| use.background | **重启** | chat.js:2805；veil_agent.py:896-898,6901 |
| 费用上限 | 立即 | web_agent.py:1529 |
| 接口/模型增删改 | 下一次请求（key 每次从 .env 读） | veil_agent.py:6444-6446 |
| 指令/工具说明 | 下一请求 / 下次自主 | chat.js:2324,2352；prompts/__init__.py:22-34（单例，读文件即新值） |
| character.toml 任何键 | 重启 | veil_agent.py:832 起，load_character 只在启动调用 |
| 瞄一眼开关 | 立即 | autonomy.py:1497-1504 |

**重启/更新/备份恢复后是否保留**

- models.json：data/ 被 .gitignore:11 忽略 → 更新不动；备份根含 data（backup_restore.py:23）→ 恢复后保留；文件 0600（model_settings.py:283-296）。
- .env：.gitignore:2；备份根含 .env（backup_restore.py:23），备份说明明确「含明文 API key」（408）。
- persona/instructions/：.gitignore:37；store 文档写明 never touched by update-main（prompts/store.py:4-5）；备份根含 persona（backup_restore.py:23）。
- 请求快照：在 data/<id>/request-snapshots/（request_snapshots.py:180-181,24），随 data 保留，但只留 40 个 request。
- character.toml：**受 git 跟踪**，本地改动在更新时是否被覆盖见 §7。
- 事务恢复：.env.models-<id>.pending 决定 blocked（model_settings.py:298-326），崩溃后要么回滚要么停模型请求。

---

## 6. 风险与建议分类

**U｜用户常用（建议进管理中心，页面直改）**：五个角色下拉、接口增删与 key、模型增改（billing/三价/max_input/thinking）、费用上限、指令逐条编辑、指令搜索。均已存在页面，管理中心应复用同一接口，不要新造存储。

**A｜高级（要写清影响）**：history_rounds、max_output_tokens、tools.max_output_tokens、tools.max_steps、autonomy.max_input_tokens（会反向抬高 provider 窗口）、run_budget_cny/max_tool_steps、glance.max_steps 与频率、context.work_checkpoint、reasoning_field、input_modalities。

**S｜敏感权限（需授权/审批）**：.env 里的所有 key（含内置 provider 的 api_key_env）、VEIL_KEY_*、blocked 事务状态。页面只应显示「是否已存 + 后 4 位」（model_settings.py:581-589），不得回显明文；管理中心的写 key 必须审批。

**R｜只读运行信息**：trim_note、history_window、usage_status/input_bytes/校准比、context-log.jsonl、请求快照、provider-errors.jsonl、model_display/cache_display。诊断页已可看（diagnostics-page.js:305-330）。

**P｜程序安全约束（必须底层强制，不给用户改）**：估算与校准常量、history_keep 分块、记忆预算比例、图片占位、脱敏正则、max_retries=0、重试/续接次数、超时、快照容量与 40 组上限、断点常量、editable=False 的 28 条指令、占位符校验、MISSING_PROVIDER 占位、ROLES/FOLLOW_CHAT。

**X｜冗余/废弃（给清理依据）**：
1. [memory] ombre_model（memory_agent.py:57-58,362，不可达分支）。
2. [providers.*].kind=claude_agent（veil_agent.py:855,863,882，只剩「跳过」语义）。
3. GLANCE_DEFAULT_MODEL 静态兜底（model_settings.py:28，与页面「跟随聊天模型」语义重叠）。
4. models.json 的 reasoning_field / input_modalities 字段：页面不能设、保存必丢（chat.js:2821）。
5. save() 里 migration 记录丢失路径（model_settings.py:662-663）。
6. Budget.settle 的 not tolerate_over_bound 抛错分支（1771-1772）：现有调用点全部传 True 或自行捕获，已成死路。
7. 内置 provider 的 cny_per_call（无文档、需先凑三个价格）。

**建议（按施工价值排序）**
1. 管理中心「模型」页直接复用 /api/models、/api/models/use，不要另存一份配置；否则会多出第四个真源。
2. 先把 reasoning_field/input_modalities 的「保存即丢」修掉（补 carry-forward 或让页面带上这两个键），再谈页面化——否则每次保存都在损坏本机配置。
3. 把 autonomy.max_input_tokens 改成 min(configured, provider.max_input_tokens)，或至少对 glance 也套 min；这是唯一会把请求发超模型窗口的配置。
4. save() 缺 limits 时改为沿用当前值（而非 10/300），并在响应里返回「被重置的角色」。
5. 给指令页加「恢复默认 / 查看默认原文」，这是目前唯一完全无入口的常用操作。
6. character.toml 的可调上下文项（history_rounds 等）建议只读展示 + 手改入口，不要做页内写入（它在 git 里，写入会与更新冲突）。

---

## 7. 无法确认项

| 项 | 原因 |
|---|---|
| data/peter/models.json 当前实际内容（角色选择、价格、reasoning_field 是否被手工设置过） | 只读边界：正式运行目录禁读，快照不含 data/ |
| .env 实际存在哪些变量（VEIL_KEY_*、DEEPSEEK_API_KEY、OPENAI_API_KEY） | 同上；代码只证明读法，不证明有没有 |
| data/peter/glance-settings.json 当前 enabled | 同上（两源冲突只由代码确定，实际取值未知） |
| MAX_THOUGHT_CHARS 的数值（veil_agent.py:5846 引用） | 本次未读其定义文件（thoughts.py），不敢抄 |
| 更新（update-main.cmd）对受跟踪的 character.toml 本地修改如何处理 | 时间到，未读 update-main.cmd / update-main-collisions.ps1；只能确定它受 git 跟踪 |
| 内置 provider 的 thinking 写成非法值时的厂商侧行为 | 代码只证明「不发参数」，厂商是否默认开思考无法由代码确定 |
| 是否存在 peter 之外的第二个角色（Ghost）共用本域配置 | 快照只有 characters/peter/；其余角色是否有 models.json 无法读取 |
| docs/tickets/ 中关于本域的设计结论是否与 e7e7992 一致 | 仅抽查 P3-2、134、211、317 相关行，未逐单核对 |

---

## 8. 取证边界声明

- 读取：仅 main-src 快照（只读）。未写入任何源码/数据。
- 写入：仅本文件 底稿文件。
- 未启动、未重启、未调用正式 Peter 的任何接口；未触发任何真实模型请求。

