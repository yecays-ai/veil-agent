# 域 5 取证：工具与权限（VEIL 全局配置审计）

- 取证对象：main-src 快照（main = e7e7992）
- 只读边界：仅读 main 快照；未读/未改正式运行目录
- 路径约定：下文 file:line 均相对 main 快照/
- 账号角色：characters/peter/character.toml（仓库跟踪）；本机数据 data/<角色>/*.json（未跟踪，快照内不存在 data/ 目录，故无法读取当前实际值）

---

## 1 执行链

### 1.1 角色配置装载：character.toml [tools] → Character.tools → Chat

1. veil_agent.py:899 “tools = dict(cfg['tools']) if 'tools' in cfg else None”，veil_agent.py:900 另存 raw_tools。
2. 逐键校验并归一化（veil_agent.py:901-931）：
   - enabled（:902-904，非 bool 抛 “tools.enabled must be boolean”）、python_sandbox（:905-907）、split_bubbles（:908-910）；
   - folder 转字符串（:915）、max_steps / max_result_bytes / max_output_tokens / lab_max_write_bytes / reply_later_* 走 positive_int（:916-925，veil_agent.py:728-731 只接受 type is int 且 >0，否则 “positive integer required”）；
   - tool_groups 必须是 TOOL_GROUPS 的子集（veil_agent.py:927-931，报错文案 “tools.tool_groups may only list: ”）。
3. 二次约束：max_steps >= 2（:932-933）、folder 必须相对且无 ..（:934-936）、persistent_append_file 必须是单级文件名（:937-941）、lab 与 journal 名字不能相同（:942-946）。
4. 缺 [tools] 表的兜底：当 tools 为 None 且 memory 为 native（默认）或任一 windows/screen_pop/desktop_file_read 开启时，程序自造一份只含 enabled/folder/max_steps/max_result_bytes 的字典（veil_agent.py:1008-1015），此时 max_output_tokens/lab/persistent_append_file/tool_groups 全部缺失，后续代码用各自默认值。
5. 失败行为：任何 ValueError 在启动装载阶段抛出 → 角色装载失败，Peter 不会以半套配置启动（无页面可绕过）。写入磁盘的行为：无（只读装载）。

### 1.2 工具集合构建：FilesystemTools.__init__

1. 角色边界先校验（tools_agent.py:233-240）：data/<角色> 被重定向则抛 “角色数据目录被重定向，文件工具已停止。”，capabilities.json 路径 = data/<角色>/capabilities.json（:240）。
2. 先按各自开关装配“非工作区”工具（tools_agent.py:250-290）：
   - screen_pop（:250-251）→ windows_observation（:261-265）→ windows_interaction（:266-276）→ desktop_create（:281-283）→ desktop_file_read（:284-286）。
   - 注意：这些装配在任何 [tools] 判断之前完成，所以 [tools].enabled=false 不会关掉它们（见 §3 冲突 3）。
3. [tools].enabled=false 时：记录能力清单后直接 return（tools_agent.py:320-326），工作区/MCP/网页工具不装配。
4. 工作区：folder 解析后必须落在 data/<角色> 内（tools_agent.py:304-306），且不得包含 data/<角色>/xiaohongshu（:308-310）；然后 mkdir（:311）。
5. 依赖 Node 的 MCP 文件服务：node/npx 缺失 → 只留只读工具并提示“文件工具不可用：请安装 Node.js LTS”（tools_agent.py:328-341）；_McpWorker 启动失败 → “文件工具服务没有启动”（:342-350）。
6. 实验室：_open_lab（tools_agent.py:429-447）校验 lab_max_write_bytes 为正整数（:431-432 报 “实验室单次写入上限必须是正整数。”）、lab 名合法且不等于 journal（:435-436，报 “实验室配置无效：必须是与成长日记不同的单级文件夹名。”）；目录被链接重定向则关闭实验室并给提示（:443-446）。
7. capabilities.json 写入失败 → self.enabled=False 并抛 “能力清单无法读取或保存，文件工具已停止；请保留 data 文件夹。”（tools_agent.py:547-549）——即“能力清单不可写 ⇒ 一律不给工具”。

### 1.3 单次工具调用的权限判定：tool_policy → invoke

1. tool_policy 六档（tools_agent.py:92-108）：desktop_file_read → desktop_read → desktop_control → desktop_create → allow（Web 工具与 AUTONOMY_DIRECT）→ workspace（OWN_WORK）→ lab（LAB_ONLY）→ deny（DENY 或不在任何集合）→ ask（ASK 集合）。
2. 集合定义在 tools_agent.py:38-50：ALLOW（只读+网页+GitHub+Windows+桌面读+screen_pop+浏览器+小红书）、ASK={write_file, append_file, grant_append}、DENY={move_file}、OWN_WORK={create_directory, move_lab_file, lab_file_digest, edit_file, delete_file}、LAB_ONLY={preview_lab_page, open_lab_game, run_lab_python}、NATIVE（记忆工具）、AUTONOMY_DIRECT={set_next_wake, set_wake_rhythm}。
3. 调用分派（tools_agent.py:1180-1200）先按工具族交给各自子系统，最后落到 tool_policy；deny 或不在 self.tools → “This tool is not authorized for the current role. Choose an available tool; permissions cannot be bypassed.”（:1197-1199）。
4. ask 档（tools_agent.py:1229-1251）：目标已存在直接拒绝（overwrite 不允许）→ approval(...) 弹卡 → 非 y 拒绝 → 回答后再验一次 → 以 open('x') 独占创建。autonomous 时 approval 返回 NOT_ASKED（agent_types.py:31-33）→ 直接拒绝并回复 “Lan is not asked during your own autonomous activity, so this was not done...”。
5. 工作区工具：_checked_own_path（tools_agent.py:1269-1280）拒绝绝对路径/盘符/.. /Windows 非法名；越界 → “The path is outside the configured workspace or cannot be resolved safely...”。
6. 结果上限：tools_agent.py:1252-1258，超过 cfg.tools['max_result_bytes'] 截断并追加 “[已截断，共 N 字节]”。
7. 生长日记追加：先读 append-grant.json 比对 path/device/inode/ctime_ns/size（tools_agent.py:1734-1746），不一致就必须本次审批（:1745-1750），批准后 O_APPEND 写入并复核 inode/size（:1753-1772），granted 时刷新 grant（:1773-1774）。grant_append 只为配置里的 journal 发长期授权（tools_agent.py:1709-1729，提示 “允许 Peter 今后持续追加到 ... 吗？本次输入 y 后重启仍有效。y/N ”）。

### 1.4 MCP 调用链

1. 配置路径：data/<角色>/mcp-services.json（veil_agent.py:2237）。
2. 初始化（mcp_services.py:379-500）：读文件 → _validate；文件不存在用 {'enabled': False, 'services': []}（:394）——即默认 MCP 全关；读坏只记 errors['_config']（:397-401），服务全部不可用但不影响聊天。
3. 内置集成（github / xiaohongshu）来自 character.toml [web]（veil_agent.py:2227-2230），只在文件里缺少该 adapter 时补一行（mcp_services.py:404-430）。
4. 每次启动按 initial_services 刷新 managed=nowhere 行的 command/args/cwd/permission（mcp_services.py:431-470），direct_grant 缺失或 false 时强制 enabled=False（:445-446、:489-490）。
5. 工具暴露：schemas()（mcp_services.py:622-647）只取 config.enabled 且 service.enabled 的服务；名字 mcp__<service>__<tool>，超 64 字符或非 [A-Za-z0-9_] 或 inputSchema > 12000 字节则不提供（:649-659）；每服务最多 40 个工具（:228-229）。
6. 调用：invoke()（mcp_services.py:898-949）先查总开关/服务开关，再按 permission 决定是否弹卡（:929-935，提示 “Peter 想调用 MCP 服务 ... 的 ...。同意吗？y/N ”），call 超时 CALL_TIMEOUT=20 秒（:16）。
7. 断线自愈：HEAL_DELAYS=(15,60,300,900,1800) 秒（mcp_services.py:23-24），启动超时 60 秒 + 看门狗 65 秒（:18-20），失败原因进 view().error 并显示在 MCP 页。
8. 失败行为：服务未启用 → “MCP 服务已停用。”；未连上 → “MCP 工具不可用：服务 X 当前没有连上（原因）。程序会自动重试；可调用 mcp_status 查看或立即重连。”

### 1.5 审批链（交互式）与自主活动审批链

1. 交互式：web_agent.py:1799-1826 approval() 压入 approval 事件 → 前端按钮 → POST /api/approve（web_agent.py:3411 → _post_approve:2749-2756，只接受 answer 为 y/n）→ hub.answer 只允许回答“当前正在等的那一条”（web_agent.py:1828-1835）→ 等待上限 APPROVAL_WAIT_SECONDS=600 秒（web_agent.py:111、1808-1818），超时返回 'timeout' 并提示 “审批等待超时，操作没有执行。”。
2. 自主活动：autonomy 的 ask 换成 _capture_approval（autonomy.py:1129-1136）→ 写入 data/<角色>/pending-approvals.json，返回 not_asked；页面在活动记录页“需要你决定”卡片（desktop/chat.html:87；chat.js:1673-1715）以 POST /api/pending-approvals（web_agent.py:3447-3452 → decide_pending:559-563）批准/拒绝，下一次唤醒以 APPROVALS FROM LAN 文本告知（pending_approvals.py:117-133，最多 5 pending + 5 未告知）。
3. 审批持久化：pending_approvals.py:12-14 TTL_HOURS=24、MAX_PROMPT=600；:86 只保留最近 200 条；:58-68 过期改 status=expired。KEEP_DAYS=7（:12）在代码中未被引用（死常量）。
4. 一次性/记住：MCP 与工具审批都是一次性（下次重新问）；唯一的“记住”是 append-grant.json（生长日记长期追加）与 windows-approved-apps.json（Lan 在“可操作的软件”里添加的软件）。

### 1.6 工具分组装配链（ticket 216/357）

1. 启动时 Chat 取 tool_groups（veil_agent.py:2293）并调用 tools.announce_tool_groups（:2295-2296）→ 写 capabilities.json 的一次性提示（tools_agent.py:848-860）。
2. 每次请求装配：tool_schemas()（veil_agent.py:2488-2503）→ effective_group(name, configured) 为 None 的常驻；属于未展开组的移出并生成 expand_capability（tool_groups.py:145-154）；有进行中 maintenance 时该工具被钉住（veil_agent.py:2492-2494）。
3. expand_capability（veil_agent.py:2562-2577）：聊天里打开后保留 CHAT_WARM_REQUESTS+1 = 4 次请求，自主唤醒只保留当次（:2572），每次请求开头递减（:2578-2584）。
4. 只影响“是否随请求带上”，不影响权限（tool_groups.py:1-4 明确写死）。

### 1.7 调用限制与失败契约

1. max_steps：veil_agent.py:5583-5593 configured_steps = tools.max_steps；自主唤醒用 autonomy.max_tool_steps（:5588-5590）；glance 用 glance.max_steps（min）；自主时 +1 给最后一轮收尾（:5593）。用尽：:6049-6059 输出 “The tool-step limit for this request has been reached.” 并中断调查记录。
2. 循环去重（ticket 416 语义）：veil_agent.py:6271-6318，仅 LOOP_DEDUP_TOOLS（:63-69）在“相同参数 + 相同结果”时压缩为 “Same result as step N; no new information...”，失败重复提示 “Same failure as step N; change the arguments or stop.”；消息工具不压缩（:64、:6280-6281）。
3. 失败契约：tool_failures.py:122-152 finalize 统一渲染；无修复建议时追加 “[program] No further guidance was added; use the reason above and do not repeat the same call unchanged.”；凭据先脱敏（tool_failures.py:86-114）。
4. 单工具结果字节上限与工具清单预算见 §2。

---

## 2 配置清单表

> 说明：状态列“仅改文件”＝没有任何页面/接口；“代码写死，无入口”＝常量。所有提示文案为原文照抄。

### 2.1 [tools] 与相关表（角色配置 character.toml）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| tools | enabled | veil_agent.py:902-904、912；tools_agent.py:298-300、320-326 | characters/peter/character.toml:121，TOML bool | true | true/false | 仅改文件 | 优先于 [web]/MCP 的服务开关：为 false 时 mcp_tools 整体为空（veil_agent.py:2450） | 重启（重新装载角色） | 是，仓库跟踪 | false 只关“工作区+MCP+网页+实验室”，不关 Windows/桌面读/screen_pop（tools_agent.py:250-290 先装配） | U |
| tools | folder | veil_agent.py:915、934-936；tools_agent.py:301-311 | character.toml:122，字符串 | data/<角色>/workspace | 相对路径；不得绝对、不得含 ..；必须落在 data/<角色>/ 内；不得包含 data/<角色>/xiaohongshu | 仅改文件 | 无覆盖 | 重启 | 是 | 越界抛 “文件工具文件夹必须位于该角色自己的 data 目录。”（tools_agent.py:306）；包含小红书登录数据抛 :310 | A |
| tools | max_steps | veil_agent.py:916、932-933；veil_agent.py:5583-5593 | character.toml:123，正整数 | 6 | 整数 ≥2；单位=工具调用轮数 | 仅改文件 | 自主唤醒被 [autonomy].max_tool_steps（character.toml:150=60）覆盖；glance 由 [glance].max_steps 覆盖 | 下一个请求 | 是 | 用尽后本轮结束并记录 step_limit；无页面可调 | U |
| tools | max_result_bytes | veil_agent.py:917；tools_agent.py:1252-1258 | character.toml:128，正整数 | 8192 | 正整数，单位=字节 | 仅改文件 | Windows 工具被夹到 ≤8192（windows_interaction.py:1040、windows_observer.py:313）；小红书再夹 ≤8192（xiaohongshu_tools.py:364）；web_research 下限 12288（web_tools.py:396） | 下一个请求 | 是 | 调大对 Windows/MCP 无效（隐性钳制） | A |
| tools | max_output_tokens | veil_agent.py:918；veil_agent.py:6064-6065、2485-2487 | character.toml:117，正整数 | cfg.max_output_tokens（缺省 1024，veil_agent.py:1158） | 正整数，单位=tokens | 仅改文件 | 只作用于“工具循环里后续请求”（replace(self.cfg, max_output_tokens=...)），最后一轮仍用 cfg.max_output_tokens | 下一个请求步 | 是 | 直接影响成本；提示文案里 1500 字符上限写死（veil_agent.py:2483-2485） | A |
| tools | persistent_append_file | veil_agent.py:919、937-941；tools_agent.py:414-419、1712、1736 | character.toml:118，字符串 | 无（None） | 单级文件名（Path(name).name == name，非 ''/'.'/'..'）；且不能与 lab 同名 | 仅改文件 | 无 | 重启 | 是 | 只有它允许“免审批长期追加”，且需 grant_append 授权 | S |
| tools | lab | veil_agent.py:920、942-946；tools_agent.py:433-447 | character.toml:119，字符串或 false | 'lab' | false=关闭；否则单级、Windows 合法名、≠ journal | 仅改文件 | 无 | 重启 | 是 | 实验室=工作区内免确认区；被链接重定向即关闭 | A |
| tools | lab_max_write_bytes | veil_agent.py:921；tools_agent.py:429-432、1567-1569、1639-1640 | character.toml:120，正整数 | 200000 | 正整数，单位=字节 | 仅改文件 | 同时下发 windows_interaction_tools（tools_agent.py:451） | 重启 | 是 | 超限直接拒绝写入（提示含具体字节数） | A |
| tools | python_sandbox | veil_agent.py:905-907；tools_agent.py:364 | character.toml:129，bool | false | true/false | 仅改文件 | 无 | 重启 | 是 | true 才注册 run_lab_python；容器隔离见 §2.6 | S |
| tools | split_bubbles | veil_agent.py:908-910、914；veil_agent.py:2925-2932 | character.toml 未写，用默认 | true | true/false | 仅改文件 | 自主唤醒一律不拆（:2925） | 下一个请求 | 是 | 最多 MAX_BUBBLES=4 段，且不超过发送余量 room（:2931-2932） | U |
| tools | reply_later_max_hours | veil_agent.py:922；veil_agent.py:3191-3193 | 未写，用默认 | 24 | 正整数，单位=小时 | 仅改文件 | 无 | 下一个请求 | 是 | 超范围提示含实际小时数 | U |
| tools | reply_later_max_deferrals | veil_agent.py:923；veil_agent.py:3204-3206 | 未写，用默认 | 2 | 正整数 | 仅改文件 | 无 | 下一个请求 | 是 | 提示文案写死 “at most 2 times”，改配置后文案不跟着变（隐性不一致） | X/A |
| tools | reply_later_max_pending | veil_agent.py:924；veil_agent.py:3201-3202 | 未写，用默认 | 3 | 正整数 | 仅改文件 | 无 | 下一个请求 | 是 | 提示写死 “At most 3 reply_later reminders” | X/A |
| tools | reply_later_daily_wakes | veil_agent.py:925；veil_agent.py:3280-3282 | 未写，用默认 | 20 | 正整数，单位=次/日 | 仅改文件 | 无 | 下一个请求 | 是 | 超限后不再由该机制唤醒 | A |
| tools | tool_groups | veil_agent.py:927-931；tool_groups.py:73 | character.toml:127，字符串数组 | 空元组=不分组，全部工具随每个请求 | 只能是 TOOL_GROUPS 的成员：web, github, desktop_control, memory_admin, build, desktop_files, garden_forum, garden_play, garden_more, nowhere, xiaohongshu, other, social | 仅改文件（页面只能看：实时上下文页工具清单） | 无 | 重启 | 是 | 写错启动即失败，报 “tools.tool_groups may only list: ...”；分组不改变权限 | A |
| tool_aliases | 工具别名表 | tool_aliases.py:14-32；characters/peter/character.toml:239- | character.toml [tool_aliases]，键值均为函数名 | {} | 名称 [A-Za-z0-9_-]{1,64}；别名≠原名；别名之间唯一；别名不能与其他原工具名冲突 | 仅改文件 | 覆盖模型看到的工具名（不改实现） | 重启 | 是 | 校验失败报 “tool aliases must use valid function names” / “must be unique within a role” 等 | A |

### 2.2 tool_groups.py 常量（代码写死）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 分组 | ALWAYS_ON（常驻工具集） | tool_groups.py:12-32 | 代码常量 frozenset | send_message/recall_message/card_action/stay_silent/reply_later/stickers/screen_pop、记忆类、自有文件类、屏幕只读类、唤醒/长任务、expand_capability/mcp_status | 新增工具必须在此或某组，否则落入 other | 代码写死，无入口 | 优先于任何组配置 | 立即（下次请求装配） | 否 | 改动风险高：影响每个请求的 token 预算 | P |
| 分组 | GROUP_TOOL_NAMES | tool_groups.py:34-47 | 代码常量 dict | web/github/desktop_control/memory_admin/build/desktop_files/garden_forum/xiaohongshu | 精确名集合 | 代码写死，无入口 | 名称命中优先于前缀 | 立即 | 否 | 删除组名会让工具掉到 other | P |
| 分组 | GROUP_TOOL_PREFIXES | tool_groups.py:49-55 | 代码常量 dict | github→mcp__github__；garden_play→mcp__galatea_garden__nostos_；garden_more→mcp__galatea_；nowhere；xiaohongshu | 最长前缀优先（:97-102） | 代码写死，无入口 | 前缀匹配晚于精确名 | 立即 | 否 | 新增 MCP 服务默认成 mcp_<id> 组（:103-104） | P |
| 分组 | GROUP_SUMMARIES / LEGACY_SUMMARIES | tool_groups.py:56-72 | 代码常量 | 13 组英文说明 + social 旧组说明 | 文案 | 代码写死，无入口 | — | 立即 | 否 | 会原样进入 expand_capability 描述 | P |
| 分组 | SOCIAL_PARTS / TOOL_GROUPS | tool_groups.py:71-73 | 代码常量 tuple | social = garden_forum + garden_play + garden_more + nowhere + xiaohongshu | 列表 | 代码写死，无入口 | 配置里写 social 等价列全部子组（:115-116、:127-129） | 重启 | 否 | 兼容旧配置 | X |
| 分组 | CHAT_WARM_REQUESTS | tool_groups.py:76 | 代码常量 int | 3 | 整数，单位=后续聊天请求数（实际保留 +1=4 次，veil_agent.py:2572） | 代码写死，无入口 | 自主唤醒忽略它（只保留当次） | 立即 | 否 | 越大越省 token 越不方便 | P |
| 预算 | BUDGET | tool_groups.py:81 | 代码常量 dict | {'always_on': 36000, 'tool': 1500, 'group': 12000} | 字节 | 代码写死，无入口 | inventory() 只用 always_on 与 group（:196-200） | 启动与每次刷新 | 否 | 'tool' 键运行时不校验，只有 tests/test_tool_budget.py:102 检查 | X |
| 预算 | OVERSIZE_EXEMPT | tool_groups.py:82-83 | 代码常量 frozenset | hold/trace/long_task_update/screen_pop/workbench/maintenance/delegate/windows_canvas_action | 名单 | 代码写死，无入口 | 仅测试引用（tests/test_tool_budget.py:102），运行时无引用 | — | 否 | 注释写“takes no new members”，实际是死名单 | X |
| 分组 | SERVICE_GROUP_PREFIX | tool_groups.py:78 | 代码常量 | 'mcp_' | 字符串 | 代码写死，无入口 | 配置里列 other 时连未知服务一起挡（:117-119） | 立即 | 否 | — | P |
| 分组 | REGROUP_FLAG | tool_groups.py:84 | 代码常量 | 'tool_groups_regrouped_357' | 一次性提示标记 | 代码写死，无入口 | 写在 capabilities.json 里 | 每次启动检查 | 是（在 capabilities.json） | 只影响提示 | X |

### 2.3 权限派生表与能力清单

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 权限分级 | ALLOW / ASK / DENY | tools_agent.py:38-43 | 代码常量 | ALLOW=只读+网页+GitHub+Windows+桌面+screen_pop+浏览器+小红书；ASK={write_file, append_file, grant_append}；DENY={move_file} | 集合 | 代码写死，无入口 | 决定 tool_policy 结果 | 重启 | 否 | deny 档一律回 “permissions cannot be bypassed” | P |
| 权限分级 | OWN_WORK / LAB_ONLY / AUTONOMY_DIRECT / NATIVE | tools_agent.py:44-54 | 代码常量 | 见源码 | 集合 | 代码写死，无入口 | workspace/lab 档免审批 | 重启 | 否 | — | P |
| 权限派生 | TOOL_ACCESS 表（r=长任务只读，w=副作用，d=委派可用） | tool_access.py:11-51 | 代码常量 dict | 41 个工具名→标志串 | 标志字符 r/w/d | 代码写死，无入口 | 派生 long_tasks.SIDE_EFFECT_TOOLS/READ_ONLY_TOOLS（long_tasks.py:16-17）与 delegate_agent.READ_ONLY_TOOLS（delegate_agent.py:22） | 启动 | 否 | 表里没有的 MCP 服务默认按“有副作用”处理（mcp_services.needs_checkpoint 默认 True，mcp_services.py:671-686） | P |
| 能力清单 | version | tools_agent.py:36、459 | data/<角色>/capabilities.json，JSON | 1 | 整数 | 代码写死，无入口 | 变更会写一条 “The capability manifest version changed.” | 启动/活动状态变化 | 是 | 改文件无效：下次启动用配置重算并覆盖 | R |
| 能力清单 | folder | tools_agent.py:301、459 | capabilities.json | 工作区目录名 | 字符串 | 自动派生 | 来自 [tools].folder | 启动 | 是 | — | R |
| 能力清单 | tools{名:权限档} | tools_agent.py:459-460、455-546 | capabilities.json | 由 tool_policy 逐工具计算 | allow/ask/workspace/lab/web/desktop_* | 自动派生（页面只读展示：设置页“能力记录” desktop/chat.html:260、chat.js:1770-1780） | 完全由代码决定，不是用户可编辑清单 | 启动 | 是 | 写入失败⇒工具全停（tools_agent.py:547-549） | R |
| 能力清单 | lab{folder,max_write_bytes} | tools_agent.py:461-462 | capabilities.json | 实验室存在时才有 | 对象 | 自动派生 | 来自 [tools].lab / lab_max_write_bytes | 启动 | 是 | — | R |
| 能力清单 | resident_memory / messaging | tools_agent.py:463-466 | capabilities.json | 由 memory.enabled / tools.enabled 派生 | bool | 自动派生 | — | 启动 | 是 | — | R |
| 能力清单 | pending_event / pending_event_model | tools_agent.py:532-533、654-672 | capabilities.json | '' | 字符串 | 自动派生 | 一次性注入给模型 | 下一次请求 | 是 | 消费后清空（:636-648） | R |
| 一次性提示标志 | tool_groups_announced / *_announced | tools_agent.py:654-672、853-860 | capabilities.json | 无 | bool | 自动 | — | 启动 | 是 | — | R |
| 追加授权 | version/path/device/inode/ctime_ns/size | tools_agent.py:1774-1782 | data/<角色>/append-grant.json，JSON | 无文件=无授权 | version==1；path 必须等于配置 journal；四项文件指纹必须匹配 | 仅能通过 grant_append 工具 + 终端 y 授权生成 | 优先级高于本次审批（有授权即不再问） | 下次 append_file 调用 | 是 | 文件被替换/增长会失配 → 重新要求审批（tools_agent.py:1734-1746） | S |
| 审批队列 | TTL_HOURS | pending_approvals.py:13、22-25 | 代码常量 | 24 | 小时 | 代码写死，无入口 | 构造参数可覆盖（无调用方覆盖） | 启动 | 否 | 过期自动改 expired | P |
| 审批队列 | MAX_PROMPT | pending_approvals.py:14、71 | 代码常量 | 600 | 字符 | 代码写死，无入口 | 超出截断 | 写入时 | 否 | — | P |
| 审批队列 | KEEP_DAYS | pending_approvals.py:12 | 代码常量 | 7 | 天 | 代码写死，无入口 | 代码中无任何引用 | — | 否 | 死常量，实际只按 TTL 与 200 条裁剪（:86） | X |
| 审批队列 | items 上限 | pending_approvals.py:86 | data/<角色>/pending-approvals.json | 200 | 条 | 自动 | 超出丢最旧 | 每次 add | 是 | — | R |
| 审批等待 | APPROVAL_WAIT_SECONDS | web_agent.py:111、1808-1818 | 代码常量 | 600 | 秒 | 代码写死，无入口 | 超时=不做，返回 timeout | 每次弹卡 | 否 | 超时提示 “审批等待超时，操作没有执行。” | P |
| 审批回答 | /api/approve answer | web_agent.py:2749-2756 | HTTP JSON | 只接受 'y'/'n' | 枚举 | 页面按钮（聊天页审批卡） | 只能回答当前 pending（:1828-1835） | 即时 | 否 | 无法重放/批量批准 | S |
| 已批准软件 | windows-approved-apps.json | tools_agent.py:275；windows_interaction.py:1069-1090 | data/<角色>/windows-approved-apps.json | 无 | label + process_paths + 固定 APPROVED_ACTIONS（8 个动作） | 设置页“可操作的软件”卡片（desktop/chat.html:234-237；chat.js:2220-2235）；POST /api/windows-apps（web_agent.py:3071-3088） | 与 windows-apps.toml 合并，setdefault 不覆盖 TOML（:1084-1085） | 即时（页面增删立即生效，无需重启） | 是 | 只能删自己加的；写死动作集不含 set_text | S |

### 2.4 MCP（data/<角色>/mcp-services.json + MCP 页）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MCP | enabled（总开关） | mcp_services.py:394、475-476、568、903 | data/<角色>/mcp-services.json，JSON bool | false（文件不存在时 {'enabled': False}） | true/false | MCP 页总开关（desktop/chat.html:140；chat.js:2760）；POST /api/mcp（web_agent.py:3427-3428） | 关掉后所有 MCP 工具（含内置 github/xiaohongshu）立即不可用 | 保存后即时（同一进程重建 worker） | 是 | 校验失败 “MCP 总开关无效。” | U |
| MCP | services[].id | mcp_services.py:14、98-99 | JSON 字符串 | 无 | ^[a-z][a-z0-9_]{0,31}$，不含 __，不以 _ 结尾 | MCP 页表单（chat.html:145，pattern/pattern 提示 “小写开头，a-z 0-9 _，不以 _ 结尾”，maxlength 32） | 决定工具前缀 mcp__<id>__ | 保存后即时 | 是 | 报错 “服务 ID 只能使用小写字母、数字和下划线，并以字母开头。” | U |
| MCP | command | mcp_services.py:102-106、56-77 | JSON 字符串 | 无（必填） | 非空、≤500 字符、无 NUL | MCP 页表单（chat.html:146，maxlength 500） | — | 保存后即时 | 是 | 本机任意可执行程序，程序不审内容（.bat/.cmd 走 pythonw 隐藏启动，:47-77） | S |
| MCP | args | mcp_services.py:107-110 | JSON 字符串数组 | [] | ≤30 项，每项 ≤2000 字符，无 NUL | MCP 页文本域（每行一个，chat.html:147） | — | 保存后即时 | 是 | 报错 “启动参数必须是字符串列表。” | S |
| MCP | cwd | mcp_services.py:110-112、578-585 | JSON 字符串或 null | null | ≤1000 字符；目录必须存在，否则 error “工作目录不存在。” | MCP 页表单（chat.html:148） | — | 保存后即时 | 是 | 不存在即服务失败，不影响聊天 | A |
| MCP | permission | mcp_services.py:113-115、661-669、929-935 | JSON 枚举 | 'ask' | ask / direct | MCP 页下拉（chat.html:149，“每次询问”/“直接调用”） | direct 跳过审批；内置集成恒为 direct（:97、:667） | 下一次调用 | 是 | ask 在自主活动时等于拒绝（NOT_ASKED） | S |
| MCP | services[].enabled | mcp_services.py:121-122、568、903-905 | JSON bool | true（缺省） | true/false | MCP 页每卡片开关（chat.js:2731-2750） | 与总开关同时为真才暴露 | 保存后即时 | 是 | 报错 “服务开关无效。” | U |
| MCP | builtin / adapter | mcp_services.py:88-97、404-430；veil_agent.py:2227-2237 | JSON（内置行） | 无 | adapter ∈ {github, xiaohongshu}；permission 恒 direct；command/args 空 | MCP 页可开关，但不可删/改命令 | 内置行由 [web] 开关决定“是否首次创建”，之后以 JSON 为准 | 保存后即时 | 是 | 报错 “内置 MCP 服务配置无效。”；每个 adapter 只能一个 | A |
| MCP | managed / direct_grant | mcp_services.py:116-120、431-470、504-521 | JSON（乌有乡行） | managed=null；direct_grant=false | managed 只能是 'nowhere'；direct_grant bool | MCP 页可开关（需先授权） | true 才能启用；启动时用初始配置覆盖 command/args/cwd/permission | 保存后即时 | 是 | 报错 “启用乌有乡直接调用前，必须由用户明确授权。”；无授权自动 enabled=false | S |
| MCP | 数量上限 | mcp_services.py:477-485 | JSON 结构校验 | — | ≤23 行；非内置 ≤20（含乌有乡时仍 20 行非内置）；内置 ≤2；nowhere ≤1 | 自动校验 | — | 保存时 | 是 | 报错 “最多配置 20 个本地 MCP 服务、1 个乌有乡服务及 2 个内置集成。” | P |
| MCP | CALL_TIMEOUT | mcp_services.py:16 | 代码常量 | 20 | 秒 | 代码写死，无入口 | — | 每次调用 | 否 | 超时即失败并记 error | P |
| MCP | START_TIMEOUT / START_WATCHDOG | mcp_services.py:18-20、222、859-863 | 代码常量 | 60 / 65 | 秒 | 代码写死，无入口 | 超时提示 “MCP 服务启动超时（超过 60 秒），请稍后点停用再启用重试。” | 启动/重连 | 否 | 只在页面显示错误 | P |
| MCP | HEAL_DELAYS / HEAL_POLL | mcp_services.py:23-24 | 代码常量 | (15, 60, 300, 900, 1800) / 5 | 秒 | 代码写死，无入口 | 仅 [tools].enabled 为真时启动自愈（veil_agent.py:2239-2240） | 后台 | 否 | 自动重连不改变权限 | P |
| MCP | 每服务工具上限 | mcp_services.py:228-229、892 | 代码常量 | 40 | 个 | 代码写死，无入口 | 超出部分不提供，页面提示 “该服务工具超过 40 个，仅提供前 40 个。” | 连接时 | 否 | — | P |
| MCP | 工具名/参数 schema 上限 | mcp_services.py:649-659、637-643 | 代码常量 | 名 ≤64 字符、schema ≤12000 字节 | 字节/字符 | 代码写死，无入口 | 超限该工具静默不提供 | 连接时 | 否 | — | P |

### 2.5 文件系统与工作区

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 工作区 | workspace 根 | tools_agent.py:304-311 | 目录 data/<角色>/<folder> | data/<角色>/workspace | 必须位于角色 data 目录内且不含小红书目录 | 仅改文件 | 所有路径检查以此为边界（:1260-1300） | 重启 | 是 | 链接/重定向一律拒绝（_redirected:124-127） | S |
| 工作区 | 版本保留（previous versions） | tools_agent.py:1698-1706；desktop_files.keep_previous_version | data/<角色>/own-work-versions/、data/<角色>/own-work-versions/desktop/ | 开 | — | 代码写死，无入口 | — | 每次覆盖/删除 | 是 | 覆盖/删除可恢复 | P |
| 桌面写 | MAX_CREATE_BYTES | desktop_files.py:28、333-335 | 代码常量 | 524288（512 KB） | 字节 | 代码写死，无入口 | — | 每次创建 | 否 | 超出拒绝并说明上限 | P |
| 桌面读 | MAX_READ_BYTES | desktop_files.py:29、632 | 代码常量 | 262144（256 KB） | 字节 | 代码写死，无入口 | 文档类改用 MAX_DOCUMENT_BYTES | 每次读 | 否 | — | P |
| 桌面读 | MAX_DOCUMENT_BYTES | desktop_files.py:30、632 | 代码常量 | 5242880（5 MB） | 字节 | 代码写死，无入口 | .docx/.pdf 用此值 | 每次读 | 否 | — | P |
| 桌面读 | MAX_DOCUMENT_TEXT | desktop_files.py:31、450、663 | 代码常量 | 500000 | 字符 | 代码写死，无入口 | 超出截断 | 每次读 | 否 | — | P |
| 桌面读 | MAX_IMAGE_BYTES / MAX_IMAGE_PIXELS | desktop_files.py:33-34、700-719 | 代码常量 | 5242880 / 25000000 | 字节 / 像素 | 代码写死，无入口 | — | 每次读图 | 否 | 超限不发图给模型 | P |
| 桌面读 | MAX_LIST_ENTRIES / MAX_SEARCH_VISITS | desktop_files.py:35-36、531-557、571-591 | 代码常量 | 100 / 5000 | 条 / 次 | 代码写死，无入口 | 超出截断并标注 | 每次 | 否 | — | P |
| 桌面 | 允许目录 | desktop_files.py:38-45 | 代码常量 | 写：desktop/documents/pictures；读：再加 downloads | Windows Known Folder GUID + 英文名兜底 | 代码写死，无入口 | 只在这些目录内操作 | 每次 | 否 | 敏感文件名/扩展名过滤（desktop_files.py:46-50） | P |
| 桌面 | CREATE_EXTENSIONS | desktop_files.py:37 | 代码常量 | .txt .md .html .htm .svg .csv .json .css .xml | 白名单 | 代码写死，无入口 | 其他后缀不可创建 | 每次 | 否 | 不产生可执行文件 | P |
| 实验室 | DEFAULT/MAX_TIMEOUT_SECONDS | lab_sandbox.py:20-21、245-249 | 代码常量 | 15 / 30 | 秒 | 代码写死，无入口 | 参数可请求，夹在 1..30 | 每次执行 | 否 | 超时 kill 容器 | P |
| 实验室 | MAX_OUTPUT_BYTES / MAX_OUTPUT_LINES | lab_sandbox.py:22-23、447 | 代码常量 | 51200 / 1000 | 字节 / 行 | 代码写死，无入口 | 截断 | 每次执行 | 否 | — | P |
| 实验室 | MAX_SCRIPT_BYTES | lab_sandbox.py:24、222-224 | 代码常量 | 2097152（2 MB） | 字节 | 代码写死，无入口 | — | 每次执行 | 否 | 超限拒绝 | P |
| 实验室 | MAX_GENERATED_FILES / MAX_FILE_BYTES / MAX_TOTAL_OUTPUT_BYTES / MAX_WORKSPACE_DISK_BYTES | lab_sandbox.py:25-28、339-341、584-587 | 代码常量 | 20 / 5 MB / 20 MB / 50 MB | 个 / 字节 / 字节 / 字节 | 代码写死，无入口 | 超限丢弃生成文件或终止 | 每次执行 | 否 | — | P |
| 实验室 | 容器隔离 | lab_sandbox.py:389-410 | docker run 参数 | --network none、--read-only、--cap-drop ALL、--pids-limit 64、--memory 256m、--cpus 1.0、tmpfs /tmp 16m | 固定 | 代码写死，无入口 | python_sandbox=true 才启用 | 每次执行 | 否 | 主机不执行 Python | P |
| 工作台 | MAX_COPY_BYTES / MAX_COPY_FILES | workbench_scope.py:29-30、179 | 代码常量 | 200 MB / 20000 | 字节 / 个 | 代码写死，无入口 | 复制项目快照时超限即停 | 每次快照 | 否 | — | P |

### 2.6 浏览器与网络

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| web | enabled | veil_agent.py:1111-1113、1127；web_tools.py:188-190 | character.toml:209，bool | false | true/false | 仅改文件 | 关掉后 web_search/web_fetch/web_research 不注册（WebTools 直接 return）；同时参与小红书内置集成的初始创建（veil_agent.py:2229-2230） | 重启 | 是 | — | U |
| web | browser_interaction | veil_agent.py:1115、1119-1120、1128；browser_tools.py:181-183 | character.toml:207，bool | false | true/false | 仅改文件 | 需 enabled 同时为真才注册 browser 工具 | 重启 | 是 | 报错 “web.browser_interaction must be boolean” | U |
| web | xiaohongshu_readonly | veil_agent.py:1116-1118、1129；xiaohongshu_tools.py:361-363；tools_agent.py:242-247 | character.toml:206，bool | false | true/false | 仅改文件 | 运行时被 MCP 页的 enabled 覆盖；FilesystemTools 为适配器强制 enabled=True（tools_agent.py:242-247） | 重启 | 是 | 见 §3 冲突 1 | S |
| web | github_readonly | veil_agent.py:1114、1122-1127；github_readonly.py:85-87；veil_agent.py:2228 | character.toml:211，bool | false | true/false | 仅改文件 | 关掉后 GitHubReadTools 无工具，但 MCP 页仍显示内置 github 行 connected（见 §3 冲突 2） | 重启 | 是 | 只读；写操作在代码里没有入口 | S |
| web | github_repository | veil_agent.py:1121-1127、1130；github_readonly.py:90-93 | character.toml:212，字符串 | '' | 正则 [A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100} | 仅改文件 | 只用于描述文本，不构成权限 | 重启 | 是 | 报错 “Invalid public GitHub repository” | A |
| 浏览器 | MAX_BYTES | browser_tools.py:22、283、310 | 代码常量 | 8192 | 字节 | 代码写死，无入口 | 结果压缩循环按此上限 | 每次调用 | 否 | — | P |
| 浏览器 | TEXT_BYTES | browser_tools.py:23 | 代码常量 | 4500 | 字节 | 代码写死，无入口 | 单页正文上限 | 每次调用 | 否 | — | P |
| 浏览器 | CONNECT 并发与流量上限 | browser_tools.py:84-86、100-104 | 代码常量 | 64 条连接 / 100 MB/会话 | 个 / 字节 | 代码写死，无入口 | 超限断开 | 每会话 | 否 | 只允许 127.0.0.1 回环代理 + 公网 IP 固定（:73-100） | P |
| 浏览器 | 禁止动作正则 FORBIDDEN / CREDENTIAL | browser_tools.py:26-31 | 代码常量 | 登录/支付/下载/上传/发布/评论/点赞/收藏/关注/消息/发送 等 | 正则 | 代码写死，无入口 | URL 与控件命中即拒绝 | 每次 | 否 | 结构性只读（无表单提交、无按钮点击） | P |
| 浏览器 | 会话步数/时长 | browser_tools.py:44（仅描述文案） | 工具描述 | 20 步、5 分钟 | — | 代码写死，无入口 | 描述里声明优先于其他目标 | 每次 | 否 | 未在代码中找到对应常量（见 §7） | R |
| 网页 | SEARCH_ENGINES / SEARCH_TOTAL_SECONDS / SEARCH_ENGINE_TIMEOUT_SECONDS | web_tools.py:24-26、35 | 代码常量 | brave,yahoo,mojeek,startpage,duckduckgo / 20 / 8 | 秒 | 代码写死，无入口 | 逐个引擎试，首个可用即止 | 每次搜索 | 否 | — | P |
| 网页 | SEARCH_RESULT_LIMIT / MAX_QUERY_CHARS / MAX_URL_BYTES | web_tools.py:36、38-40 | 代码常量 | 8 / 500 / 2048 | 条 / 字符 / 字节 | 代码写死，无入口 | — | 每次 | 否 | 查询超长提示 “Search query must contain 1 to 500 characters.” | P |
| 网页 | DEEPSEEK_SEARCH_URL / DEEPSEEK_SEARCH_CNY / DEEPSEEK_QUERY_CHARS / DEEPSEEK_MAX_RESPONSE_BYTES | web_tools.py:29-34、60-93 | 代码常量 | api.deepseek.com/v1/tools/search；¥0.005/次；128 字符；1 MB | — | 代码写死，无入口 | DeepSeek 搜索优先（有 key 时） | 每次搜索 | 否 | 禁跟随重定向以免泄露 key（:66-70） | P |
| 网页 | RESEARCH_DEFAULT_PAGES / RESEARCH_MAX_PAGES / RESEARCH_RESULT_BYTES | web_tools.py:37-39、396 | 代码常量 | 3 / 5 / 12288 | 页 / 页 / 字节 | 代码写死，无入口 | research 结果字节取 max(max_result_bytes, 12288) | 每次 | 否 | — | P |
| 网页 | FETCH_CALL_TIMEOUT_SECONDS | web_tools.py:41 | 代码常量 | 45 | 秒 | 代码写死，无入口 | — | 每次 fetch | 否 | — | P |
| 网页 | DDGS_PROXY | web_tools.py:44-63 | 进程环境变量（os.environ），不是 .env | 无（回退系统代理） | URL 字符串 | 仅改文件/系统环境变量 | 优先于系统代理 | 进程启动 | 否 | 代理地址中的账号密码在报错时脱敏（:65-68） | A |
| 网页 | DEEPSEEK_API_KEY | web_tools.py:31、283-291；.env.example:1 | .env（dotenv_values 读取，不做环境变量回退） | 无 | 非空字符串 | 仅改文件（.env） | 缺失则跳过 DeepSeek 搜索，改用引擎列表 | 每次搜索 | 是 | 只在本地 .env 读，不外发；密钥文件属敏感 | S |
| 小红书 | MAX_DAILY / MIN_INTERVAL | xiaohongshu_tools.py:37-38、388-391 | 代码常量 | 60 次/日 / 10 秒 | 次 / 秒 | 代码写死，无入口 | 计数存 data/<角色>/xiaohongshu/read-budget.json（:375-391） | 每次读 | 是（预算文件） | 预算文件读不出来时拒绝请求（:385-387） | P |
| 小红书 | MAX_RESPONSE / MAX_IMAGES / MAX_IMAGE_BYTES / MAX_IMAGES_TOTAL | xiaohongshu_tools.py:36、44-46 | 代码常量 | 1 MB / 3 张 / 2.5 MB / 5 MB | 字节/张 | 代码写死，无入口 | — | 每次读 | 否 | 只读，登录必须人工（:57-58） | P |

### 2.7 Windows 操作

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 观察 | [windows_observation] enabled | veil_agent.py:951-958；tools_agent.py:261-265 | characters/peter/character.toml:217，bool | false | true/false | 仅改文件 | 缺表即 false | 重启 | 是 | 只读观察；报错 “windows_observation.enabled must be boolean” | U |
| 交互 | [windows_interaction] enabled | veil_agent.py:959-965、973-982；tools_agent.py:266-280 | character.toml:225，bool | false | true/false | 仅改文件 | 需 Windows 平台（os.name=='nt'） | 重启 | 是 | 白名单不存在直接启动失败（:976-977） | S |
| 交互 | apps_file | veil_agent.py:966-969、976-977 | character.toml:226，字符串 | 'windows-apps.toml' | 单级文件名、必须以 .toml 结尾、必须在 characters/<角色>/ 内且存在 | 仅改文件 | 无 | 重启 | 是 | 报错 “the Windows app allowlist must exist inside this character folder” | S |
| 交互 | idle_seconds | veil_agent.py:970-972；tools_agent.py:271；windows_interaction.py:1041 | character.toml:228，整数 | 3 | 1..30 秒（装载时校验；运行中再夹一次 max(1,min(x,30))） | 仅改文件 | 无 | 重启 | 是 | 报错 “windows_interaction.idle_seconds must be from 1 to 30” | U |
| 交互 | windows-apps.toml applications | characters/peter/windows-apps.toml:4-26；windows_interaction.py:363-391、419-432 | characters/<角色>/windows-apps.toml，TOML | notepad、chrome（public_web=true） | 每个 app：label、launch_path（可省）、process_paths（必填，支持 * 通配）、actions（必须是 _ALLOWED_ACTIONS 子集）、public_web（bool） | 仅改文件 | 与 windows-approved-apps.json 合并，TOML 优先（setdefault） | 重启 | 是 | 报错 “{app_id} needs an explicit list of supported interaction actions.” 等 | S |
| 交互 | _ALLOWED_ACTIONS | windows_interaction.py:239-247（定义）、:378-381（校验） | 代码常量 | activate/minimize/restore/invoke/select/toggle/expand/collapse/set_text | 集合 | 代码写死，无入口 | TOML 与页面批准的软件都受此限制 | 启动 | 否 | 不含键盘/鼠标原始注入 | P |
| 交互 | MAX_RESULT_BYTES | windows_interaction.py:40、1040；windows_observer.py:33、313 | 代码常量 | 8192 | 字节 | 代码写死，无入口 | 与 [tools].max_result_bytes 取小（max(512,min(x,8192))） | 每次调用 | 否 | — | P |
| 交互 | MAX_KEYBOARD_FALLBACK_CHARS | windows_interaction.py:304、701-703 | 代码常量 | 200 | 字符 | 代码写死，无入口 | 超出改用其他方式或拒绝 | 每次 | 否 | — | P |
| 交互 | APPROVAL_IDLE_SECONDS / APPROVAL_IDLE_WAIT_SECONDS | windows_interaction.py:306-307、509-514 | 代码常量 | 1 / 4 | 秒 | 代码写死，无入口 | 用户批准后等 1 秒无输入才动手 | 每次 | 否 | 人类输入优先 | P |
| 观察 | MAX_WINDOWS / MAX_CONTROLS / MAX_TREE_DEPTH / MAX_WINDOW_TITLE / MAX_CONTROL_TEXT / MAX_TEXT_CHARS | windows_observer.py:28-34、654-660、984 | 代码常量 | 40 / 120 / 8 / 180 / 1200 / 4000 | 个 / 层 / 字符 | 代码写死，无入口 | 超出截断 | 每次 | 否 | — | P |
| 观察 | MAX_SCREENSHOT_BYTES / MAX_CAPTURE_PIXELS / MAX_ABANDONED_READS | windows_observer.py:35-36、43 | 代码常量 | 5 MB / 8000000 / 3 | 字节 / 像素 / 次 | 代码写死，无入口 | 截图走视觉模型兜底 | 每次 | 否 | — | P |
| 弹幕 | [screen_pop] enabled | veil_agent.py:992-999；tools_agent.py:250-251 | character.toml:221，bool | false | true/false | 仅改文件 | 缺表即 false | 重启 | 是 | 报错 “screen_pop.enabled must be boolean” | U |
| 弹幕 | MAX_TEXT_CHARS / MAX_DANMAKU_DURATION / count 上限 | screen_pop.py:11-13、31-38、74 | 代码常量 | 500 字符 / 20 秒 / 10 层 | — | 代码写死，无入口 | schema 与规范化双重校验 | 每次 | 否 | — | P |
| 桌面读 | [desktop_file_read] enabled | veil_agent.py:983-990；tools_agent.py:284-286 | character.toml:232，bool | false | true/false | 仅改文件 | 缺表即 false | 重启 | 是 | 只读；报错 “desktop_file_read.enabled must be boolean” | U |
| 短便签 | [short_term_notes] enabled | veil_agent.py:1000-1007 | character.toml:237，bool | false | true/false | 仅改文件 | 缺表即 false | 重启 | 是 | 属记忆域（域 2/3），仅列此以说明本域装配不涉及 | X |

### 2.8 通道与外部服务（本域边界）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| QQ | enabled / poll_seconds / idle_seconds / daily_max_sent | character.toml:198-202；veil_agent.py:1101-1109；qq_channel.py:219-228 | character.toml [qq]，bool/整数 | false / 20 / 120 / 100 | poll、idle、daily 正整数；daily 单位=条/日 | 仅改文件 | 需 .env 有 QQ_LAN_CONTACT 且 win32 | 重启 | 是 | 只在键盘鼠标空闲 idle_seconds 后读写；文本通道，无工具权限 | A |
| 邮件 | enabled / poll_seconds / daily_max_sent | character.toml:191-193；veil_agent.py:1095-1100；email_channel.py:99-108 | character.toml [email]，bool/整数 | false / 60 / 20 | 正整数 | 仅改文件 | 需 .env 三个值齐全，否则静默不启用 | 重启 | 是 | 三值缺一即 return None（:105-107） | A |
| 密钥 | PETER_EMAIL_ADDRESS / PETER_EMAIL_AUTH_CODE / LAN_EMAIL_ADDRESS / QQ_LAN_CONTACT | .env.example:9-12；email_channel.py:105-107；qq_channel.py:224-227 | .env（项目根），dotenv_values(interpolate=False, utf-8-sig) | 空=不启用通道 | 字符串 | 仅改文件 | 空值等价关闭 | 重启 | 是 | 敏感凭据；不进日志（通道错误文本由各模块裁剪） | S |
| 工作台 | workbench.json enabled / project_roots / container_image / web_hosts | veil_agent.py:798-828、1132-1147；workbench_switch.py:11-19、44-79 | data/<角色>/workbench.json，JSON | enabled=false；project_roots=[]；container_image='veil-workbench'；web_hosts=[] | 只允许这 4 个键；project_roots 每个非空字符串路径；web_hosts 主机名、≤50 个（veil_agent.py:802-807） | 设置页“工作台”卡片（desktop/chat.html:225-232；chat.js:2195-2210）；GET/POST /api/workbench（web_agent.py:3101-3140） | 覆盖 [executor] 同名键（veil_agent.py:1132-1134） | 重启（workbench_switch 文档注明；页面保存后状态即时显示，Peter 侧下次启动生效） | 是 | 拒绝把 data/characters/persona 或它们的祖先/子目录加入（workbench_switch.py:51-58）；最多 50 个网站 | S |
| 工作台 | [executor] command / max_seconds / repeat_limit / run_budget_cny / wake_budget_cny | veil_agent.py:1136-1147；executor_opencode.py:40 | character.toml（若存在）+ workbench.json 覆盖 | command=''；max_seconds=600；repeat_limit=3；run_budget_cny='0.5'；wake_budget_cny='0.3' | 正整数/金额字符串 | 仅改文件 | workbench.json 的同名键优先 | 重启 | 是 | 报错 “executor.enabled must be boolean” | A |
| 工作台 | [executor] project_roots | veil_agent.py:792-796、1143 | character.toml + workbench.json | [] | 非空字符串路径数组 | 设置页仅写 workbench.json | workbench.json 覆盖 TOML | 重启 | 是 | 报错 “executor.project_roots must be a list of folder paths” | S |
| 工作台 | [executor] web_hosts | veil_agent.py:802-807、1145 | 同上 | [] | 主机名数组，≤50，域名正则 | 设置页仅写 workbench.json | 覆盖 TOML | 重启 | 是 | 报错 “executor.web_hosts must be a list of host names”；空=不允许浏览 | S |
| OpenCode | DEFAULT_LIMITS / PROXY_MAX_REQUESTS | executor_opencode.py:40-41 | 代码常量 | max_seconds 600、repeat_limit 3、run_budget_cny 0.5 / 600 次 | — | 代码写死，无入口 | 用 [executor] 的 limits 覆盖 | 每次任务 | 否 | 防跑飞 | P |
| OpenCode | permission 表 | executor_opencode.py:914-965 | 代码构造（OPENCODE_CONFIG_CONTENT 内联） | bash: 容器内全 allow、宿主未知命令 ask；edit 全 allow；webfetch ask；external_directory ask；secret globs ask | 规则表 | 代码写死，无入口 | 项目自带 opencode.json 无法放宽（:915-917 注释） | 每次任务 | 否 | 拒绝即换路子，任务不终止（continue_loop_on_deny） | P |
| OpenCode | ASK_COMMANDS / MAINTENANCE_DENY / SECRET_READ_GLOBS / KNOWN_ASKED | executor_opencode.py:155-176 | 代码常量 | 见源码（git push/installer/curl/sudo/rm -r 等必须问；维护模式下 git commit/push 等直接 deny） | 前缀匹配 | 代码写死，无入口 | — | 每次任务 | 否 | 自主活动期间一律拒绝（:383-384 提示 “needs Lan's approval; not asked during autonomous activity”） | P |

---

## 3 重复 / 冲突 / 隐性覆盖

1. **小红书开关两套且互相打脸**：character.toml [web].xiaohongshu_readonly（:206）只影响“内置集成行的初始创建”（veil_agent.py:2229-2230）与旧工具；MCP 适配器实例被 tools_agent.py:242-247 强制 enabled=True/xiaohongshu_readonly=True。结果：MCP 页那一行开着，小红书工具就可用，哪怕角色配置写 false。反向：MCP 页关掉，角色配置 true 也没用。
2. **GitHub 双开关 + 页面假“已连接”**：运行时真正是否给工具由 mcp-services.json 的 enabled 决定；[web].github_readonly=false 时 GitHubReadTools.enabled=False、tools=[]（github_readonly.py:85-94），但 register_adapter 仍然注册（veil_agent.py:2242-2244），mcp_services.view() 对内置行只看 adapter 是否存在（mcp_services.py:853-860）→ 页面显示 connected，工具列表为空，调用才报 “MCP 工具不可用”。
3. **[tools].enabled=false 不等于关掉所有工具**：Windows 观察/交互、桌面创建/读取、screen_pop 在 tools.enabled 判断之前就装进 self.tools（tools_agent.py:250-290），只有工作区/MCP/网页/实验室被关；Chat.tool_mode 也会因 tools.tools 非空而为真（veil_agent.py:2270-2271）。用户以为“关掉工具”，实际桌面动作仍在。
4. **max_result_bytes 被多处钳制**：Windows 工具 ≤8192（windows_interaction.py:1040、windows_observer.py:313），小红书 ≤8192（xiaohongshu_tools.py:364），web_research 反过来有 12288 下限（web_tools.py:396）。角色里写 32768 只有文件系统工具真按 32768 走。
5. **capabilities.json 不是可编辑的“直接做/先问清单”**：它由 tool_policy 每次启动重算并整体覆盖（tools_agent.py:455-546），用户手改成 ask 会在下次启动被抹掉；页面（设置页“能力记录”、实时上下文页）只读。
6. **BUDGET['tool']=1500 与 OVERSIZE_EXEMPT 运行时不生效**：inventory() 只比较 always_on/group（tool_groups.py:196-200），单工具 1500 字节只有 tests/test_tool_budget.py:102 检查；OVERSIZE_EXEMPT 全仓仅测试引用 → 死名单。
7. **pending_approvals.KEEP_DAYS=7 死常量**：只按 TTL_HOURS 过期（pending_approvals.py:58-68），保留条数靠 items[-200:]（:86）。
8. **提示文案与配置脱钩**：reply_later 上限改成非默认值后，拒绝文案仍写死 “At most 3 ... ” / “at most 2 times”（veil_agent.py:3201-3206）；tools.max_output_tokens 进提示的 1500 字符说明是常量（veil_agent.py:2483-2485）。
9. **workbench.json 被两处读、角色写死**：Chat 读 data/<cfg.id>/workbench.json（veil_agent.py:814-828），而 /api/workbench 用 workbench_switch 里 CHARACTER='peter'（workbench_switch.py:13），非 peter 角色页面改的是 peter 的文件。
10. **MCP 服务命令无内容审查**：command 允许任意本机程序（mcp_services.py:102-106），只有长度/NUL 校验；权限模型落在 permission=ask/direct 与审批卡上，属“用户显式引入即信任”。
11. **内置集成与自定义服务同名上限**：内置 adapter 只能各 1 个（:489-491）；重复会报 “内置 MCP 集成不能重复配置。”
12. **DDGS_PROXY 不是 .env 键**：web_tools.py:50 只读 os.environ，项目里没有 load_dotenv（grep 全仓仅 dotenv_values 直读 .env），写进 .env 不生效。

---

## 4 页面与入口现状

已有页面/接口（以 desktop 与 web_agent.py 为准）：

| 能力 | 页面位置 | 接口 | 可改什么 |
|---|---|---|---|
| MCP 服务增删改、总开关、permission、启用/停用、重连 | desktop/chat.html:138-152；desktop/chat.js:2684-2770 | GET /api/mcp（web_agent.py:2324-2332）、POST /api/mcp（:2836-2851，需 revision 防并发，409 冲突文案 “MCP 配置已在另一个窗口改变，请重试。”） | mcp-services.json 全部字段（内置行的命令不可改） |
| 审批卡（交互式） | 聊天页审批气泡；POST /api/approve（web_agent.py:2749-2756、3411） | 只有 y/n，一次性 | 当前 pending 的一条 |
| 待决定请求（自主活动积压） | 活动记录页“需要你决定”（chat.html:87；chat.js:1673-1715） | GET/POST /api/pending-approvals（web_agent.py:2451-2460、3447-3452） | pending-approvals.json 单条批准/拒绝 |
| 可操作的软件（Windows 白名单增删） | 设置页卡片（chat.html:234-237；chat.js:2220-2235） | GET/POST /api/windows-apps（web_agent.py:2872-2879、3071-3088） | 只能加“当前已打开”的窗口或删自己加的；TOML 白名单不可在页面改 |
| 工作台（开关、项目目录、可访问网站） | 设置页卡片（chat.html:225-232；chat.js:2195-2210） | GET/POST /api/workbench（web_agent.py:3101-3140） | data/peter/workbench.json 的 enabled/project_roots/web_hosts（container_image 无页面） |
| 工具清单（只读） | 实时上下文页“工具清单”（chat.html:105；chat.js:1483-1501） | 随 /api/context 轮询（web_agent.py:500、687-716） | 只读：每个工具字节数、所属组、最近调用次数、超预算提示 |
| 能力记录（只读） | 设置页“常用工具→能力记录”（chat.html:260；chat.js:1770-1780、2030） | 随轮询下发 | 只读 capabilities.json 摘要 |
| 模型设置页 | 设置页（chat.html 模型卡片） | /api/models | 与本域无关，仅影响力（预算）不涉工具权限 |

只能改文件（无任何页面）：
- [tools] 全部键、[web] 全部键、[windows_observation]/[windows_interaction]/[desktop_file_read]/[screen_pop] 的 enabled、windows-apps.toml、tool_aliases、[executor] 的 command/max_seconds/repeat_limit/run_budget_cny/wake_budget_cny/container_image、.env（DEEPSEEK_API_KEY 等）。

代码写死，无入口（只能改代码）：
- tool_policy 六档与 ALLOW/ASK/DENY/OWN_WORK/LAB_ONLY、TOOL_ACCESS 表、tool_groups 的 ALWAYS_ON/GROUP_*/BUDGET/OVERSIZE_EXEMPT/CHAT_WARM_REQUESTS、MCP 的 CALL_TIMEOUT/START_TIMEOUT/HEAL_DELAYS/40 工具上限/schema 上限、approval 600 秒、pending TTL 24 小时/200 条、桌面与 Windows 各 MAX_*、lab_sandbox 容器参数、browser_tools 的 MAX_BYTES/FORBIDDEN/100 MB、web_tools 的引擎列表与超时、xiaohongshu 的 60 次/日与 10 秒、executor_opencode 的 ASK_COMMANDS/MAINTENANCE_DENY/权限表。

---

## 5 生效时机与持久化

| 配置 | 生效时机 | 重启后 | 更新（git pull/update.bat）后 | 备份恢复后 |
|---|---|---|---|---|
| character.toml 全部键 | 重启进程/重载角色 | 保留（读文件） | 仓库跟踪，可能被更新覆盖/冲突（update-main-collisions.ps1 处理） | 若备份含 characters/ 则回到备份版本 |
| mcp-services.json | 保存即生效（同一进程重建 worker；页面 409 冲突需刷新） | 保留 | data/ 未跟踪，保留 | 回到备份版本 |
| capabilities.json | 启动时重算并写回 | 保留但不生效（被重算） | 保留 | 回到备份，随后被重算 |
| append-grant.json | 下一次 append_file | 保留（授权跨重启，提示文案明确写“重启仍有效”） | 保留 | 回到备份；文件指纹不匹配即失效 |
| pending-approvals.json | 下一次唤醒读取 | 保留 | 保留 | 回到备份 |
| windows-approved-apps.json | 页面增删立即生效 | 保留 | 保留（不写 TOML，避免更新冲突） | 回到备份 |
| workbench.json | Peter 侧重启（页面自身状态即时刷新） | 保留 | 保留（ticket 239 专门为“更新不撤销”而设计） | 回到备份 |
| .env | 重启（各模块启动时读取） | 保留 | 保留（未跟踪） | 回到备份 |
| 代码常量 | 改代码 + 重启 | — | 随仓库更新变化 | — |

---

## 6 风险与建议分类

**U（用户常用，建议进管理中心直接改）**：[tools].enabled、folder、max_steps、max_output_tokens、split_bubbles、reply_later_max_hours、[web].enabled、browser_interaction、windows_observation/interaction 的 enabled、idle_seconds、screen_pop.enabled、desktop_file_read.enabled、MCP 总开关/服务启用/permission（已有页面）、workbench.json。

**A（高级，需说明影响）**：max_result_bytes（会被静默钳制）、lab、lab_max_write_bytes、reply_later_max_deferrals/max_pending/daily_wakes、tool_groups、tool_aliases、github_readonly/github_repository、[executor] 预算与时限、windows-apps.toml 的 actions 与 process_paths。

**S（敏感权限，需授权/审批）**：persistent_append_file + append-grant.json、python_sandbox、MCP command/args/permission（任意本机程序）、managed=nowhere 的 direct_grant、[windows_interaction].enabled/apps_file、workbench.json 的 project_roots/web_hosts、.env 中的凭据、[web].xiaohongshu_readonly。

**R（只读运行信息）**：capabilities.json 全表、工具清单 inventory、MCP view 的 status/error/tools、pending-approvals 列表、append-grant 当前值、tools.jsonl 调用统计。

**P（程序安全约束，必须底层强制、不给用户改）**：tool_policy 六档与各集合、TOOL_ACCESS、TOOL_GROUPS 常量、CALL_TIMEOUT/START_TIMEOUT/HEAL_DELAYS/40 工具上限、approval 600 秒、pending TTL、桌面/Windows 各 MAX_*、lab_sandbox 容器参数、browser PublicProxy 与 FORBIDDEN、xiaohongshu 日限与冷却、executor 权限表与 MAINTENANCE_DENY、capabilities 不可写即停工具。

**X（冗余/废弃，给清理依据）**：pending_approvals.KEEP_DAYS、tool_groups.OVERSIZE_EXEMPT、tool_groups.BUDGET['tool']（仅测试）、tool_groups.SOCIAL_PARTS（兼容旧配置）、reply_later 写死文案、[short_term_notes]（默认关，属记忆域）。

**建议的页面分组**（给全局管理中心）：
1. 工具与能力（本域 U）：工具分类开关、每类说明、指向现有 MCP 页；
2. 权限与审批：审批卡历史、待决定列表（复用 /api/pending-approvals）、已记住的授权（append-grant、已批准软件）一键吊销；
3. 高级：max_steps/结果上限/预算/分组/别名，带“会被钳制到 X”的提示；
4. 运行信息：工具清单与预算占用（已有只读数据源）。

---

## 7 无法确认项

1. **当前实际值**：快照内没有 data/ 目录（未跟踪），故 data/peter/mcp-services.json、capabilities.json、append-grant.json、pending-approvals.json、windows-approved-apps.json、workbench.json 的真实内容与当前值无法读取；表中“代码默认值”是代码层默认，不是本机现值。
2. **browser 的“20 步 / 5 分钟”**：仅出现在工具描述（browser_tools.py:44），未能在 browser_tools.py 中找到对应常量或强制逻辑（可能是别处或描述性承诺），标记为无法确认，不能当作可配置项。
3. **brain 权限过滤**（任务书提到的 brain 权限）属记忆/大脑域，本域代码路径里未见与工具权限联动的实现（tools_agent/mcp_services 均无 brain 调用），无法在本域确认；需要记忆域取证员交叉核对。
4. **guest_profiles.py 的清理阈值**（POLL_SECONDS=5、STALE_AFTER_DEAD_OWNER=24h、LEGACY_STALE_AFTER=7d、WATCH_ATTEMPTS=1440、STALE_ANYWAY=7d，见 guest_profiles.py:18-26）是 Chrome Guest 临时目录清理参数，与工具权限无直接关系，未列入主表；如需纳入，应作为“浏览器临时数据”独立小节。
5. **watchdog/自愈是否在无页面时也运行**：代码只在 [tools].enabled 为真时 start_healing（veil_agent.py:2239-2240），但 MCP 页的“重连”按钮（/api/mcp?retry=1）在 tools 关闭时仍可调用——此路径下是否真的重建 worker 未逐行验证。
6. **/api/workbench 与角色切换**：workbench_switch.CHARACTER='peter' 写死，多角色场景下页面行为未验证（快照只有 peter 角色目录），无法确认是否已有其他角色实例使用。
7. **tests 覆盖**：仅查看了 tests/test_tool_budget.py 对 BUDGET/OVERSIZE_EXEMPT 的引用，未逐一核对其他测试对权限表的断言。
