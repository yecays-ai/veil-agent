# 域 7 · 交互与个性化 —— VEIL 全局配置取证（只读审计）

审计快照：main 快照（main = e7e7992 完整快照）。
范围：桌面壳/外观/localStorage、聊天与消息表现、人格与提示词资料、语音输入输出、QQ/邮件/离开桌面通道、日历待办提醒。
所有路径相对 main-src。未读取正式运行目录 正式运行目录，未修改任何源码或数据。

---

## 1. 执行链（文件/键 → 读取 → 校验 → 使用点 → 缺省/非法/耗尽）

### 1.1 桌面壳（Electron 主进程）
- **本机数据目录**：process.env.VEIL_DESKTOP_DATA → 打包态 %APPDATA%\VEIL Desktop → 开发态 desktop/.runtime-data（desktop/main.cjs:33-36）；userData/sessionData 由它派生（desktop/main.cjs:36-37）。
- **desktop.json**：settingsPath() = userData/desktop.json（desktop/main.cjs:38）→ 启动时 Object.assign(settings, JSON.parse(readFile))（desktop/main.cjs:830），读不到时打包态回退旧路径 <exe>/data/desktop.json（desktop/main.cjs:832-837）→ 每次变更 save() 全量重写（desktop/main.cjs:41-49），高频操作走 180 ms 防抖 saveSoon（desktop/main.cjs:50-53）。非法 JSON：catch 后沿用内存默认 { size: 320, theme: 'light' }（desktop/main.cjs:14）。
- **桌宠尺寸**：chat:set-pet-size IPC（desktop/main.cjs:689-694）/ 拖角 pet:resize-move（desktop/main.cjs:788）→ createPet() 内 Math.max(160, Math.min(720, Math.round(size)))（desktop/main.cjs:136）→ 重建 BrowserWindow（desktop/main.cjs:140-153），窗口 resize 事件把宽度写回 settings.size（desktop/main.cjs:150-152）。非数字：chat:set-pet-size 直接 return false（desktop/main.cjs:690）；菜单入口另夹一次 160–720（desktop/main.cjs:802）。
- **主题**：渲染进程 saveTheme(theme) → IPC chat:save-theme 校验 ['light','dark'].includes(theme)（desktop/main.cjs:683-688）→ 只写 settings.theme 并推给菜单窗（desktop/main.cjs:685-686）；页面自身主题来自 localStorage（见 1.2），启动时**不回读** desktop.json 的主题（desktop/chat.js:2162 只有 saveTheme，没有 getTheme）。菜单窗用 settings.theme 上色（desktop/main.cjs:241,244；desktop/menu.js:3）。
- **背景图片**：chat:choose-background → 扩展名白名单 png/jpg/jpeg/webp（desktop/main.cjs:645,654-655）→ 超过 8 * 1024 * 1024 抛「图片必须小于 8 MB。」（desktop/main.cjs:657）→ 复制到 userData/appearance-background<ext>（desktop/main.cjs:658-660），只把扩展名存 settings.backgroundExt（desktop/main.cjs:661）→ chat:get-background 每次读文件转 base64（desktop/main.cjs:669-673）；chat:clear-background 删除四种扩展并置 null（desktop/main.cjs:675-681）。启动时文件缺失则 backgroundExt=null（desktop/main.cjs:845）。
- **窗口尺寸/置顶**：聊天窗最小 1180×760（受屏幕钳制）、默认请求 1440×900（desktop/main.cjs:175-179），alwaysOnTop: true、skipTaskbar: true、无边框（desktop/main.cjs:180-185）；关闭改为隐藏（desktop/main.cjs:187）；位置尺寸写 settings.chatBounds（desktop/main.cjs:191-193）。分离视图 fallback：chat 520×680、life 980×700、其余 430×680（desktop/main.cjs:102），最小 340×420、alwaysOnTop: true（desktop/main.cjs:106-109），边界存 settings.detachedBounds[view]（desktop/main.cjs:105,113）。菜单窗固定 254×226（desktop/main.cjs:233-236）。屏幕外坐标由 clampPosition 拉回工作区（desktop/main.cjs:61-69,88-96）。
- **托盘 / 开机自启**：代码内无 Tray、setLoginItemSettings、openAtLogin（全仓检索无命中），工单明确「不做托盘或开机自启」（docs/tickets/020-resident-service.md:5）。这两项**没有配置项**，属缺失能力而非隐藏开关。
- **单实例**：app.requestSingleInstanceLock()，失败即退出；二次启动只显示桌宠（desktop/main.cjs:826-828）。
- **麦克风权限**：权限处理器只放行聊天窗/分离聊天窗的 media + audio（desktop/main.cjs:20-32,847-850），其他网页权限一律拒绝 → 摄像头/通知等无法被页面申请。
- **屏幕弹窗/弹幕（screen-pop）**：壳每 500 ms 轮询 /api/screen-pop/next（desktop/main.cjs:440-442）；弹窗尺寸 min(520, 工作区宽) × min(270, 高)、层叠步长 24 px（desktop/main.cjs:337-346）；弹幕逐显示器各建一窗（desktop/main.cjs:356-362）、置顶到 screen-saver 层、忽略鼠标（desktop/main.cjs:263-265）；命令校验文本 1–500 字符且 UTF-8 ≤2000 字节、name ≤100、count 1–10、duration 1–20（desktop/main.cjs:323-331）。非法命令直接 return，不建窗。

### 1.2 外观与 localStorage（渲染进程）
- **APPEARANCE_KEY** = 'veil-desktop-appearance-v1'（desktop/chat.js:2125）。loadAppearance() 读 JSON，逐项 clamp：页面/整体透明度 20–100（fallback 88）、气泡 55–100（fallback 90）、背景 0–100（fallback 100）、Peter 尺寸 160–720（fallback 320）、主题只接受 'dark' 否则 'light'（desktop/chat.js:2128-2136）。JSON 解析失败返回 appearanceDefaults（desktop/chat.js:2135；默认值定义在 desktop/chat.js:2126）。
- **应用点**：applyAppearance() 设 documentElement.dataset.theme 与四个 CSS 变量 --window-alpha/--bubble-alpha/--background-opacity/--view-alpha（desktop/chat.js:2139-2153），CSS 侧只作半透明底色，**不调用 Electron 的窗口 opacity**（desktop/chat.css:73-76,103-106）。
- **写回**：滑块 oninput 即时 storeAppearance()（desktop/chat.js:2163-2168）；Peter 尺寸 onchange 才发 IPC（desktop/chat.js:2169）；启动时用主进程实际值覆盖 localStorage（desktop/chat.js:2170）。storage 事件做多窗同步（desktop/chat.js:2155-2158）。
- **自定义 CSS**：输入超过 20000 字符或命中 @import|url(|expression(|-moz-binding|behavior: 抛「CSS 不能加载外部资源或脚本」（desktop/chat.js:2500-2502），通过后写入 veil-desktop-appearance-v1-css（desktop/chat.js:2506）；chat.html:12 用 nonce 样式表承载；chat.html:6 CSP 禁 connect-src。恢复默认会清 CSS 与背景（desktop/chat.js:2507）。
- **头像**：veil-avatar，仅 localStorage；选图限制超过 8 MB →「头像超过 8MB」、宽*高>40e6 →「头像像素太多，换小一点的」，裁成 256×256 PNG dataURL 后写入（desktop/chat.js:3050-3069）。清缓存即丢。
- **侧栏/rail**：veil-sidebar-collapsed 默认折叠（值为 null 也视为折叠，desktop/chat.js:2542-2543）；veil-rail-visible 默认隐藏（desktop/chat.js:2553-2560）。
- **生活页最近 emoji**：veil-life-recent-emoji，最多 16 个（desktop/chat.js:1141-1142）。
- **语音输入草稿**：sessionStorage veil-speech-draft（desktop/chat.js:6,57,79），发送后清除（desktop/chat.js:2006）→ 关窗即丢，不跨会话。

### 1.3 聊天与消息表现
- **分泡**：split_bubbles(text, limit=MAX_BUBBLES)，MAX_BUBBLES = 4（bubble_split.py:10,14）；含三重反引号或 ~~~ 代码围栏、或 limit<2 直接返回原文（bubble_split.py:16-17）；段落多于上限时前 3 段独立、其余合并为最后一泡（bubble_split.py:21-23）。
- **逐泡出现节奏**：bubbleDelay(text) 字符数 ≤60 → 1200 ms、≤200 → 2400 ms、否则 3600 ms（desktop/chat.js:148），代码写死。
- **事件轮询**：poll() 固定 setTimeout(poll, 600)（desktop/chat.js:1711,1727）；连续 3 次失败显示离线条并禁用按钮（desktop/chat.js:1808）。生活页每 15 s 刷新（desktop/chat.js:2769），MCP 页 3 s（desktop/chat.js:2770）。
- **提示去重**：toast 同时最多 3 条、同文 30 s 内不重复（desktop/chat.js:36,40-45）；后端 notice 同文 6 h 只显示一条（desktop/chat.js:33,190-196）。消息分组间隔 15 min（desktop/chat.js:31）。**没有**系统通知、托盘红点、任务栏闪烁或未读数（全仓无 Notification/flashFrame/setOverlayIcon 命中）。
- **消息动作**：/api/message/action 允许 delete/recall/regenerate/edit/favorite（web_agent.py:1682），批量删除 ≤100 条（web_agent.py:1686），忙碌时返回「Peter 正在处理消息，请稍后再试。」（web_agent.py:1692-1693）；虚拟卡片动作另走 create_transfer/create_gift/card_*（web_agent.py:1638-1642）。
- **表情包**：常量 NAME_MAX=10、PACK_NAME_MAX=30、MAX_PACKS=60、MAX_PER_PACK=500、MAX_IMAGE_BYTES=2 MB、MAX_SVG_BYTES=40 KB、MAX_URL_CHARS=2000、下载超时 10 s/总 15 s/6 并发/3 跳（stickers.py:24-37）；库落在 <可写根>/files + index.json（stickers.py:283-286）；上传接口 body ≤3.3 MB（web_agent.py:2254）。
- **虚拟卡片**：AMOUNT_LIMIT=1e12、NOTE_LIMIT=240、ICON_LIMIT=24、GIFT_LIMIT=500（virtual_cards.py:21-25），页面入口在聊天工具条（desktop/chat.js:2621）。
- **聊天检索工具**：MAX_EXCERPTS=5、MAX_KEYWORDS=6、BROWSE_MESSAGES=30、MESSAGE_CHARS=200、RESULT_BYTES=8000、NEIGHBOUR_WINDOW=3（chat_search.py:9-15）。

### 1.4 人格 / 提示词 / 用户资料 / 关系
- **人设路径**：persona = "persona/peter.md"（characters/peter/character.toml:4）→ 启动读入 cfg.persona；Peter 页保存走 /api/peter-page 的 save_persona，只校验非空（web_agent.py:623-632，错误「人设不能为空。」），写前存版本、写后 reload_persona()（web_agent.py:630-631）。**没有字节/token 上限**，页面上只显示字符/字节/token 数、不显示上限（desktop/peter-page.js:321）。
- **背景资料**：路径由人设名派生 peter.background.md（background.py:267-269，不在 character.toml 里）→ 每轮读取，缺失/不可读即不注入（background.py:272-281）；选取上限 items 3 / characters 1500 / chunk 700 / chunks_per_paragraph 2 / keywords 30 / keyword_characters 40（background.py:16-17），首行【常驻】/core 段落常驻（background.py:22）。保存接口只校验是字符串（web_agent.py:633-639）。
- **说话样例（voice style）**：文件 peter.voice.md（voice_style.py:23-25），开关文件 data/<角色>/voice-style.json（voice_style.py:17,60），默认开（voice_style.py:62-67）；MAX_CHARS = 8000（voice_style.py:16），保存超限原文「说话样例太长（N 字），最多 8000 字；它每次请求都会带上。」（voice_style.py:88-89）；保存前 snapshot（voice_style.py:91），原子替换（92-97）；开关/正文二选一校验（web_agent.py:3198-3203）。注入位置在人设之后（veil_agent.py:4187-4190）。
- **关于你（Lan 的人设）**：persona/lan.md，max_bytes = 4000（characters/peter/character.toml:183-186；user_persona.py:16-17）→ 保存超限抛「关于你（Lan 的人设）超出上限：N / 4000 字节。」（user_persona.py:34-35），每轮读活文件（user_persona.py:27-29）。
- **Learned Context**：data/<角色>/learned-context.json，max_bytes = 4000（characters/peter/character.toml:163-169；learned_context.py:58）；facts+growth+进行中(active/uncertain)+关系(confirmed/uncertain) 共享同一预算（learned_context.py:276-285），超出报「超出共享上限 N 字节（当前 X / 4000）；没有保存。请删减 N 字节，或把内容移到另一块。」（learned_context.py:287-291）。
- **关系底色**：默认 {enabled: True, max_chars: 480, max_items: 8}（relationship_base.py:24），max_chars 只接受 80–4000、max_items 1–40（relationship_base.py:40-43），配置键为 [profile.relationship_base]（characters/peter/character.toml:174-177 注释给出默认值），经 veil_agent.py:1082-1085 归一；每轮构建（veil_agent.py:4203-4208）。
- **版本**：persona/versions/，文件名 <stem>.<yyyyMMdd-HHmmss>[-n].md（persona_versions.py:9-14,50-53），内容相同不重复存（persona_versions.py:41-47），失败只打 stderr 不阻止保存（persona_versions.py:72-73）；页面列出最近 20 个（web_agent.py:515-521）。
- **对话窗口/输出长度**：history_rounds = 80、max_output_tokens = 1024（characters/peter/character.toml:6-7）。

### 1.5 语音（输出 TTS / 输入 STT）
- **TTS 配置来源**：只有 .env。load_voice_config 要求 MINIMAX_API_KEY / MINIMAX_VOICE_ID / MINIMAX_MODEL / MINIMAX_REGION 全部非空且 region 属于 {cn, global}，否则整体返回 None（voice_output.py:44-51）；计费率 MINIMAX_CNY_PER_10K_CHARS 默认 '3.5'，必须有限且 >0，否则返回 None（voice_output.py:52-57）。主机：cn=https://api.minimaxi.com、global=https://api.minimax.io（voice_output.py:16）。
- **TTS 使用点**：[voice] provider = "minimax"（characters/peter/character.toml:9-10）→ 启动校验只允许 minimax，否则启动报「unsupported voice provider」（veil_agent.py:1056-1058）→ make_voice 配置不全则返回 None（veil_agent.py:6592-6597）→ 没有 voice 时 send_message 不接受 voice 参数（veil_agent.py:2952）→ 合成走预算预占/结算（veil_agent.py:3077-3086；voice_output.py:134-147）。请求 60 s 超时、mp3/32 kHz/128 kbps/单声道、音频 ≤25 MB、响应 ≤(2×25 MB+64 KB)（voice_output.py:14-15,99-103）。失败文案如「MiniMax 鉴权失败，请检查 API Key 和账号区域。」「MiniMax 额度不足或请求过多。」（voice_output.py:106-123）。
- **STT（听写）**：/api/transcribe（web_agent.py:3383-3385,2624-2644），mime 必须在 VOICE_TYPES（webm/ogg/m4a/wav，web_agent.py:74-79），原始 ≤10 MB（web_agent.py:54,2636）；密钥取 .env 的 EMBED_API_KEY，缺失返回「语音识别密钥未配置。」（web_agent.py:939-944）；模型写死 FunAudioLLM/SenseVoiceSmall、端点写死 https://api.siliconflow.cn/v1/audio/transcriptions、超时 45 s（web_agent.py:951-958）；并发上限 MAX_ACTIVE_VOICE_TRANSCRIPTIONS = 4（web_agent.py:55）。
- **录音上行**：语音消息 /api/voice 原始 ≤10 MB（web_agent.py:861-862），容器魔数校验（web_agent.py:864-869）；页面录音最长 120 s 自动停（desktop/chat.js:1898），上传超时 15 s（desktop/chat.js:1907），听写录音同样 120 s（desktop/chat.js:133）、前端 10 MB 上限（desktop/chat.js:105）、转写请求超时 70 s（desktop/chat.js:109）。

### 1.6 离开桌面通道（QQ / 邮件）
- **QQ**：[qq] enabled/poll_seconds/idle_seconds/daily_max_sent（characters/peter/character.toml:195-202）→ 配置归一默认 20/120/100（veil_agent.py:1103-1108）→ QQChannel.from_env 要求角色表启用、平台 win32、.env 有 QQ_LAN_CONTACT，缺一即不启用且不报错（qq_channel.py:23,219-228）→ 仅在「键鼠空闲 ≥ idle_seconds 且前台非全屏」时读窗（qq_channel.py:251-253）→ 每段发送前查当日上限（qq_channel.py:290-293，文案「今天的 QQ 消息已达到 N 条上限，剩下的没有发出。」）→ 计数来自 data/<角色>/qq-log.jsonl 的 kind=sent（qq_channel.py:209,236-249）。找不到窗口、抬头不是 Lan、剪贴板被占、输入框内容不一致都拒绝发送（qq_channel.py:371,393-395,441-461）。
- **邮件**：[email] enabled/poll_seconds/daily_max_sent（characters/peter/character.toml:188-193）→ 归一另含 smtp/imap 默认 smtp.qq.com:465、imap.qq.com:993（veil_agent.py:1094-1102）→ 需 .env 三项 PETER_EMAIL_ADDRESS / PETER_EMAIL_AUTH_CODE / LAN_EMAIL_ADDRESS 全非空（email_channel.py:29,98-108）→ 只读发件人精确等于 Lan 的未读邮件、最多 10 封（email_channel.py:224-236）→ 回信正文截 4000 字符（email_channel.py:30,242），附件 ≤3（email_channel.py:141），SMTP/IMAP 超时 30 s（email_channel.py:204,220-221），当日上限文案「今天的邮件已达到 N 封上限，这封没有发出。」（email_channel.py:185-186）。
- **离开桥接**：轮询线程按 channel.poll_seconds 等待（away_bridge.py:194-201）；「同意 / 不同意」等纯词回执可远程回答审批（away_bridge.py:31-45,173-181）。
- **移动端**：**无移动网页入口**。HTTP 服务只绑 127.0.0.1（web_agent.py:3463），Host 必须匹配 127.0.0.1:<port>/localhost:<port>（web_agent.py:1983-1987），POST 还要求 Origin 精确匹配（web_agent.py:2008-2012）；桌面页面 CSP connect-src 'none'（desktop/chat.html:6）。仓库内无手机页/远程访问实现。

### 1.7 日历 / 待办 / 约定 / 提醒
- **存储**：data/<角色>/life-records.json、life-daily-checks.jsonl、calendar-reminders.jsonl、calendar-daily-reminders.jsonl、calendar-settings.json、life-moods.json（life_records.py:92-97）。
- **自动记录开关**：calendar-settings.json 的 auto_record，文件缺失默认 **true**（life_records.py:106-111），写入前校验必须是 bool（112-120）；页面开关在生活页（desktop/chat.js:1272）→ POST /api/life {auto_record}（web_agent.py:3254-3261，body 必须只有这一个键）。
- **字段上限**：标题非空 ≤240 字符、正文 ≤12000 字符、time 仅事件且 HH:MM、日期 YYYY-MM-DD、状态枚举、remind 必须 bool、remind_before 属于 {0,15,30} 且提醒必须有事件+时间（life_records.py:172-180）。超限一律 ValueError('invalid record')。
- **提醒计算**：due = date+T+time - remind_before 分钟，且 changed > due 的排程跳过（避免改期后误报）；迟到 >3600 s 记 outcome='missed'（life_records.py:243-258），已处理按 (id, schedule_revision) 去重（241-242）。
- **每日待办提醒**：窗口 23:30 起 6.5 小时，06:00 前算前一天（life_records.py:296-300），每天最多一次（303-307）。
- **每日轻提示**：23:50–06:00 之间（life_records.py:321-324），提示语原样拼标题（341）。
- **折叠**：已完成的单次待办只显示最新 FOLD_AFTER = 30 条（life_records.py:36-43）。
- **心情**：每人每天一个，MAX_MOOD_CHARS = 16（life_records.py:46-47），不可设未来（490，文案 a mood cannot be set for a future day），必须是 emoji（491，文案 mood must be one emoji）；桌面只能写 Lan 自己那一格（web_agent.py:3283-3296）。
- **约定自动记日历**：只有开关开时才跑（calendar_agreements.py:31,85）；触发词表（13-18）；带最近 6 条对话、已有 60 天内事件（36,60-64），prompt 截断 16000/8000 字符（67-68）；模型调用固定 max_tokens=512、thinking='disabled'、记账分类 calendar-agreement（70-80）；只接受 today..+60 天、标题 ≤240、time 正则校验（91-94）；每次改写写 calendar-agreements.jsonl 并可撤销（125-132）。
- **/api/life body**：≤20000 字节（web_agent.py:3241），超限 400 invalid life record。

---

## 2. 配置清单表

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 桌面壳 | 桌宠尺寸 size / petSize | desktop/main.cjs:14,136,689-694；desktop/chat.js:2126,2168-2170 | desktop.json 的 size + localStorage veil-desktop-appearance-v1.petSize | 320 px | 160–720 px（两处同夹） | 设置页「Peter 大小」滑块（chat.html:257）+ 拖四角 | 主进程实际宽度胜出：启动时 petSize() 覆写 localStorage（chat.js:2170） | 松开滑块立即重建窗口；拖角实时 | desktop.json 持久；localStorage 冗余 | 仅聊天窗 IPC 来源校验（main.cjs:689-690） | U |
| 桌面壳 | 主题 theme | desktop/main.cjs:683-688；desktop/chat.js:2126,2133,2160-2162 | desktop.json 的 theme + localStorage 同名键 | light | light / dark | 设置页「日间/夜间」按钮（chat.html:241） | 页面看 localStorage；菜单窗看 desktop.json；互不回读 | 点击立即；下次启动菜单沿旧值 | 两处各自持久，可能不一致 | 主进程只接受 light/dark（main.cjs:684） | U |
| 桌面壳 | 背景图片 backgroundExt | desktop/main.cjs:645-681,845；desktop/chat.js:2171-2177 | userData/appearance-background<ext> + desktop.json 只存扩展名 | 无（无背景） | png/jpg/jpeg/webp；文件 ≤8 MB | 设置页「上传图片 / 移除」（chat.html:252） | 文件存在才生效；启动时缺失即清空键 | 选图后立即；重启后读同一文件 | 图片在 userData，不进 data/、不进仓库 | IPC 仅允许聊天窗/more 页（main.cjs:651,676）；MIME 白名单 | U |
| 桌面壳 | 聊天窗默认/最小尺寸 | desktop/main.cjs:175-179 | desktop.json chatBounds | 1440×900，最小 1180×760 | 受屏幕工作区钳制 | 仅拖动/最大化；无设置页 | 有 chatBounds 则用上次值 | 下次开窗 | desktop.json | 无 | U（无页面入口） |
| 桌面壳 | 分离视图尺寸 detachedBounds | desktop/main.cjs:102-113 | desktop.json detachedBounds[view] / detachedViews | chat 520×680；life 980×700；其他 430×680；最小 340×420 | ≥340×420；life 最小宽 520 | 拖出成独立窗口后拖动；无设置页 | 保存值优先，否则 fallback | 下次拖出/重启 | desktop.json | 视图名白名单 VIEW_NAMES（main.cjs:13,98） | U |
| 桌面壳 | 菜单窗尺寸 | desktop/main.cjs:233-236,243 | 无（每次按光标定位） | 254×226 | 固定 | 代码写死，无入口 | — | 每次打开 | 不持久 | 无 | P |
| 桌面壳 | screen-pop 轮询间隔 | desktop/main.cjs:440-442 | 代码常量 | 500 ms | 固定 | 代码写死，无入口 | — | 常驻 | 不持久 | 仅读本机回环接口 | P |
| 桌面壳 | screen-pop 弹窗几何 | desktop/main.cjs:337-346 | 代码常量 | min(520,宽)×min(270,高)，层叠 24 px | 数量 1–10（screen_pop.py:60） | 代码写死；数量由 Peter 调工具给 | count 来自工具参数，几何不可配 | 每次弹窗 | 不持久 | 命令逐字段校验（main.cjs:323-331） | P |
| 桌面壳 | 托盘 / 开机自启 | 无实现 | — | 不存在 | — | 代码写死，无入口（工单明确不做，docs/tickets/020-resident-service.md:5） | — | — | — | — | X（缺失能力） |
| 桌面壳 | 单实例锁 | desktop/main.cjs:826-828 | 进程内（Electron lock） | 启用 | 固定 | 代码写死，无入口 | VEIL_DESKTOP_TEST 环境变量可绕过（826） | 启动时 | 进程级 | 不可绕过 UI | P |
| 桌面壳 | 麦克风权限白名单 | desktop/main.cjs:20-32,847-850 | 代码常量 | 仅 audio、仅聊天窗/分离聊天窗 | audio 单类型 | 代码写死，无入口 | 其他权限一律拒绝 | 每次权限请求 | 不持久 | 安全边界 | P |
| 外观 | 整体透明度 windowOpacity | desktop/chat.js:2126,2132-2133,2141,2163-2164；chat.html:242 | localStorage veil-desktop-appearance-v1 | 88 | 20–100（%） | 设置页「整体透明度」 | 拖动整体会覆盖全部页面值（2163） | 拖动即时 | 仅 localStorage | 非法值 clamp 到范围/默认 | U |
| 外观 | 分页面透明度 pageOpacity[6] | desktop/chat.js:2132,2144-2146,2165；chat.html:244-249 | 同上 | 每页 88 | 20–100（%） | 设置页「单独调整页面」6 滑杆 | 单独页面值经 CSS --view-alpha 覆盖整体（chat.css:103-106） | 拖动即时 | 仅 localStorage | 无 | U |
| 外观 | 气泡透明度 bubbleOpacity | desktop/chat.js:2126,2134,2142,2166；chat.html:251 | 同上 | 90 | 55–100（%） | 设置页「聊天气泡」 | 独立于页面透明度 | 拖动即时 | 仅 localStorage | 无 | U |
| 外观 | 背景图透明度 backgroundOpacity | desktop/chat.js:2126,2134,2143,2167；chat.html:253 | 同上 | 100 | 0–100（%） | 设置页「图片透明度」 | 仅作用于背景层（chat.css:106） | 拖动即时 | 仅 localStorage | 无 | U |
| 外观 | 自定义 CSS -css | desktop/chat.js:2500-2507；chat.html:255-256 | localStorage veil-desktop-appearance-v1-css（纯文本） | 空 | ≤20000 字符；禁 @import、url(、expression(、-moz-binding、behavior: | 设置页文本框 +「应用 CSS」 | 按 key 独立于外观 JSON | 点击「应用」立即 | 仅 localStorage，清缓存即丢 | 正则黑名单（2502，文案「CSS 不能加载外部资源或脚本」）；CSP nonce 样式表（chat.html:12） | A |
| 外观 | 头像 veil-avatar | desktop/chat.js:3050-3069 | localStorage（256×256 PNG dataURL） | 内置 assets/pep.png | ≤8 MB、≤40 MP、裁 256×256 | 点头像更换（chat.js:3053-3055） | 只有 localStorage 一份 | 选图立即 | 仅 localStorage，换机/清缓存丢失 | 前端尺寸/像素校验 | U |
| 外观 | 侧栏折叠 veil-sidebar-collapsed | desktop/chat.js:2536-2543 | localStorage '1'/'0' | 折叠（null 视为 '1'） | '1'/'0' | 侧栏按钮 | 无 | 点击立即 | 仅 localStorage | 无 | U |
| 外观 | 右侧活动栏 veil-rail-visible | desktop/chat.js:2544-2561 | localStorage '1'/'0' | 隐藏 | '1'/'0' | 顶栏按钮 | 无 | 点击立即 | 仅 localStorage | 无 | U |
| 外观 | 生活页最近表情 veil-life-recent-emoji | desktop/chat.js:1141-1142 | localStorage JSON 数组 | 空 | 最多 16 项 | 无直接入口（选心情时自动记） | 与心情数据分离 | 选心情后 | 仅 localStorage | 无 | X（仅便利缓存） |
| 外观 | 听写草稿 veil-speech-draft | desktop/chat.js:6,57,79,2006 | sessionStorage | 无 | 任意文本 | 无入口（录音中自动存） | 覆盖输入框现有内容 | 录音中；发送/关窗清 | 仅会话内，重启丢 | 无 | R |
| 外观 | 恢复默认 | desktop/chat.js:2507 | 同时改 localStorage + IPC | — | 一键 | 设置页「恢复默认」 | 覆盖全部外观键、清 CSS、清背景、重置桌宠尺寸 | 立即 | 见各键 | 会不可逆删除自定义 CSS/背景 | U |
| 聊天 | 分泡上限 MAX_BUBBLES | bubble_split.py:10,14-23 | 代码常量 | 4 | ≥2 才分；≤4 泡 | 代码写死，无入口 | 每个 send_message 段独立 | 每次发送 | 不持久 | 代码围栏不拆 | P |
| 聊天 | 气泡出现节奏 bubbleDelay | desktop/chat.js:148 | 代码常量 | ≤60 字 1200 ms；≤200 字 2400 ms；否则 3600 ms | 固定三档 | 代码写死，无入口 | 与分泡数量无关 | 每条消息渲染时 | 不持久 | 无 | P |
| 聊天 | 事件轮询 | desktop/chat.js:1711,1727,1808 | 代码常量 | 600 ms | 固定 | 代码写死，无入口 | 3 次失败进离线态 | 常驻 | 不持久 | 无 | P |
| 聊天 | 提示去重/上限 | desktop/chat.js:33,36,40-45,190-196 | 内存 Map | toast 3 条 / 同文 30 s；notice 同文 6 h | 固定 | 代码写死，无入口 | 内存态，刷新即清 | 运行时 | 不持久 | 无 | R |
| 聊天 | 消息分组间隔 | desktop/chat.js:31 | 代码常量 | 15 min | 固定 | 代码写死，无入口 | 仅显示层 | 渲染时 | 不持久 | 无 | R |
| 聊天 | 消息动作白名单/批量 | web_agent.py:1637-1743 | 代码常量 + 请求体 | 允许 5 种动作；批量 ≤100 | delete/recall/regenerate/edit/favorite；卡片动作 6 种 | 聊天消息菜单（desktop/chat.js:631-721） | 忙碌时全部拒绝 | 立即 | 写聊天记录 | 忙碌门 + id 正则；删除/撤回不可撤销 | A |
| 聊天 | 表情包库限制 | stickers.py:24-37,283-286；web_agent.py:2250-2273 | <可写根>/files + index.json | 名称 10 字、包名 30 字、60 包、每包 500、图 2 MB、SVG 40 KB、URL 2000 字符 | 见左 | 聊天表情面板上传/管理（desktop/chat.js:2586-3030），API /api/stickers* | 前端与后端双重限制 | 上传即时 | 落盘（角色数据目录） | 下载有重定向/大小限制 | U/A |
| 聊天 | 虚拟卡片金额与文本 | virtual_cards.py:21-25,72,87-108；desktop/chat.js:2621 | 聊天记录内 JSON | 金额上限 1e12；备注 240；图标 24；礼物文案 500 | 金额 >0 且 ≤1e12；文本见左 | 聊天工具条「转账/礼物」卡片 | 与消息动作同一忙碌门 | 发送即时 | 写聊天记录 | 金额上限硬校验 | U |
| 聊天 | 聊天检索工具参数 | chat_search.py:9-15 | 代码常量 | 5 段摘录 / 6 关键词 / 30 条浏览 / 200 字 / 8000 字节 / 邻接 3 | 固定 | 代码写死，无入口 | 结果上限与工具预算一致 | 每次调用 | 不持久 | 只读聊天记录 | R |
| 聊天 | screen_pop 工具参数 | screen_pop.py:11-13,59-75；characters/peter/character.toml:219-221 | 代码常量 + 角色开关 | 文本 ≤500；count 1–10（仅 popup）；duration 默认 12、1–20 s；color #RRGGBB；animation scroll/fade/bounce；position top/center/bottom | 见左 | 由 Peter 调工具；用户只能开关 [screen_pop] enabled | 全屏时 popup 自动降级为 danmaku（hub_screen_pop.py:92-96） | 每次弹 | 不持久 | 锁屏/全屏时拒绝或降级 | A（参数）/S（打扰） |
| 人格 | 人设文件 persona | characters/peter/character.toml:4；web_agent.py:586-632,668-682；desktop/peter-page.js:321 | characters/peter/persona/peter.md（本机，不入库） | 无文件则空 | 非空字符串；**无字节上限** | Peter 页人设卡片（chat.html:31 → desktop/peter-page.js:386） | 以本机文件为唯一来源；保存后 reload_persona | 保存后下一轮 | 文件 + persona/versions | 仅本机回环 token；保存前自动版本 | U（缺上限→A） |
| 人格 | 背景资料 | background.py:16-22,267-281；web_agent.py:633-639；desktop/peter-page.js:326 | persona/peter.background.md | 无文件则不注入 | 文件任意；每轮最多 3 段 / 1500 字符 | Peter 页「背景资料」卡片 | 关键词匹配 + 【常驻】优先；超出预算按段裁剪 | 下一轮 | 文件 + versions | 只读注入；保存只校验字符串 | U/A |
| 人格 | 说话样例 | voice_style.py:16-17,23-25,62-107；web_agent.py:3195-3214；desktop/chat.js:1502-1525；chat.html:104 | 正文 persona/peter.voice.md；开关 data/<角色>/voice-style.json | 开关 true（文件缺失视为开）；正文空 | 清理后 ≤8000 字 | 上下文页「他平时怎么说话」+ 开关/保存 | 开关文件优先于正文；分离窗口只读（chat.js:1509） | 保存后下一句 | 文件；删开关文件=恢复默认开 | 每请求都带，硬上限防膨胀；保存留版本 | U |
| 人格 | 关于你（Lan 人设） | characters/peter/character.toml:183-186；user_persona.py:14-52 | persona/lan.md | 空 | ≤4000 字节（UTF-8） | Peter 页「关于你」卡片 | 独立于 learned context 预算 | 下一轮（每轮读活文件） | 文件 + versions | 超限拒绝并给出 N/4000 | U |
| 人格 | Learned Context 预算 | characters/peter/character.toml:163-169；learned_context.py:58,276-291；desktop/peter-page.js:300-313 | data/<角色>/learned-context.json | 4000 字节 | facts+growth+进行中+关系 共享 4000 字节 | Peter 页四张卡片，实时显示「已用 / 4000 字节」 | 与关系底色共享正文，预算独立计算 | 保存后下一轮 | 文件 + learned-context.versions.jsonl | 超限报差额并拒绝；带 expected_updated_at 防并发覆盖 | U |
| 人格 | 关系底色 | relationship_base.py:24,33-44；veil_agent.py:1082-1085,4203-4208 | character.toml [profile.relationship_base]（当前被注释）+ 数据在同 json | enabled=true；max_chars=480；max_items=8 | max_chars 80–4000；max_items 1–40；enabled bool | Peter 页「关系」卡片编辑条目；容量只能改 toml | 只渲染 confirmed/uncertain；outdated 不渲染 | 下一轮 | 条目在 json，容量在 toml | 非 int 或超范围静默回落默认（不报错） | U（容量 A） |
| 人格 | 版本保留 | persona_versions.py:9-14,31-71；web_agent.py:515-521 | persona/versions/*.md | 每次内容变化存一份 | 时间戳文件名，无数量上限 | Peter 页「恢复」按钮（web_agent.py:668-682） | 恢复前先把当前内容存一份 | 保存时 | 文件 | 恢复只认 versions 目录内文件（677） | R |
| 人格 | 历史轮数 / 输出上限 | characters/peter/character.toml:6-7 | character.toml | 80 轮；1024 tokens | 正整数 | 仅改文件 | provider max_input_tokens 另有限制（character.toml:17） | 重启 | 文件 | 无 | A |
| 语音 | MiniMax 四项密钥 | voice_output.py:16,44-51 | .env：MINIMAX_API_KEY / MINIMAX_VOICE_ID / MINIMAX_MODEL / MINIMAX_REGION | 缺失即整体禁用 | 四项全非空；region 属于 cn / global | 仅改文件（.env），无页面 | [voice].provider 必须先为 minimax | 重启（启动时加载） | .env 明文，不进库 | 任一缺失静默隐藏能力，日志不落密钥（voice_output.py:1） | S |
| 语音 | 语音计费率 | voice_output.py:52-57,136-138 | .env MINIMAX_CNY_PER_10K_CHARS | "3.5" 元/万字符 | 有限且 >0 的十进制 | 仅改文件 | 非法→整个 TTS 禁用 | 重启 | .env | CJK 双字计费（billed_characters，62-63） | A |
| 语音 | TTS provider | characters/peter/character.toml:9-10；veil_agent.py:1056-1058 | character.toml [voice] | 无 | 只能 minimax | 仅改文件 | 决定是否创建 voice 实例 | 重启 | 文件 | 非法值导致启动失败 | A |
| 语音 | 音频/响应上限与超时 | voice_output.py:14-15,99-103 | 代码常量 | 音频 25 MB；响应 50 MB+64 KB；HTTP 60 s；mp3 32 kHz/128 kbps/单声道 | 固定 | 代码写死，无入口 | 超出报「MiniMax 返回的音频过大。」 | 每次合成 | 不持久 | 响应体有界读取（66-82） | P |
| 语音 | 识别密钥/模型/端点/超时 | web_agent.py:939-958 | .env EMBED_API_KEY；模型与端点写死 | 模型 FunAudioLLM/SenseVoiceSmall；45 s | 密钥非空 | 仅改文件（密钥）；模型无入口 | 与向量嵌入共用同一 key | 每次识别 | .env | 缺失提示「语音识别密钥未配置。」；HTTPS 固定域名 | S（密钥）/P（模型） |
| 语音 | 上传与录音限制 | web_agent.py:54,74-79,861-869,2636；desktop/chat.js:105,109,133,1898,1907 | 代码常量 + 前端 | 10 MB；120 s；听写超时 70 s；语音上传 15 s | mime 白名单 4 种 + 容器魔数 | 代码写死，无入口 | 前端先截断，后端再校验 | 每次录音 | 不持久 | 双端校验，防伪造容器 | P |
| 语音 | 转写并发 | web_agent.py:55 | 代码常量 | 4 | 固定 | 代码写死，无入口 | — | 运行时 | 不持久 | 防刷 | P |
| 渠道 | QQ 通道 | characters/peter/character.toml:195-202；veil_agent.py:1103-1108；qq_channel.py:23,204-307 | character.toml [qq] + .env QQ_LAN_CONTACT + data/<角色>/qq-log.jsonl | enabled=false（加载器）；当前 toml=true；poll 20 s；idle 120 s；daily 100 | 正整数；仅 win32 生效 | 仅改文件；无页面 | .env 缺联系人 → 功能静默关闭 | 重启 | toml/.env 文件；日志 jsonl | 只在空闲且非全屏时读窗；发送前抬头校验 Lan；失败一律不发 | S |
| 渠道 | 邮件通道 | characters/peter/character.toml:188-193；veil_agent.py:1094-1102；email_channel.py:29-30,98-108,185-186 | character.toml [email] + .env 三项 + data/<角色>/email-log.jsonl | enabled=false（加载器）；当前 toml=true；poll 60 s；daily 20；smtp.qq.com:465；imap.qq.com:993 | 正整数；三项 env 全非空 | 仅改文件；无页面 | 三项 env 缺一 → 静默关闭 | 重启 | 文件；日志 jsonl | 只读 Lan 地址；收件人固定；正文截 4000；附件 ≤3 | S |
| 渠道 | 离开桥接 | away_bridge.py:31-45,134-160,194-201 | 代码常量 + 通道配置 | 审批词表；轮询按通道 poll_seconds；发送队列等待 2 s | 固定 | 代码写死，无入口 | 通道自己的 ready_to_send 决定能否发 | 运行时 | 不持久 | 只接受纯肯定/否定词回答审批 | P |
| 渠道 | 移动网页入口 | 无实现；web_agent.py:1983-2012,3463 | — | 只绑 127.0.0.1 | Host/Origin 精确匹配 | 代码写死，无入口 | — | — | — | 天然拒绝跨机访问 | X（需求缺口） |
| 日历 | 自动记日历开关 | life_records.py:96,106-120；web_agent.py:3254-3261；desktop/chat.js:1272 | data/<角色>/calendar-settings.json | true（文件缺失） | bool | 生活页按钮「聊天约定自动记日历：开/关」 | 关时约定识别直接 return（calendar_agreements.py:31） | 点击立即 | 文件 | 文件损坏抛错→读取 500 | U |
| 日历 | 记录字段上限 | life_records.py:172-180 | 代码常量（写 life-records.json） | 标题 240；正文 12000；remind_before 0/15/30 | 见左；time 仅事件 HH:MM | 生活页表单（desktop/chat.js:1253-1258） | 后端校验为准，前端另限 200 字输入（chat.js:1244） | 保存时 | 文件 | 严禁删除 Lan 写的记录（life_records.py:223-224） | U |
| 日历 | 提醒提前量 | life_records.py:25,165-166,179-180；desktop/chat.js:203 | 记录字段 remind_before | 0（准点） | 0 / 15 / 30 分钟 | 生活页「提醒我」下拉；聊天提示条下拉（准点/提前 15/提前 30） | 有 time 才可提醒 | 保存后由轮询取用 | 随记录持久 | 非法值整条拒绝 | U |
| 日历 | 提醒判定与补报 | life_records.py:232-294 | calendar-reminders.jsonl 追加 | 迟到 >60 min 记 missed；按 (id, schedule_revision) 去重 | 秒/分钟由代码算 | 代码写死，无入口 | 改期后旧排程不再触发（253） | 后台轮询 | jsonl | 只补报未处理过的 | P |
| 日历 | 每日待办提醒窗口 | life_records.py:296-319 | calendar-daily-reminders.jsonl | 23:30 起 6.5 h；06:00 换日 | 固定 | 代码写死，无入口 | 每天一次 | 到点 | jsonl | 只提醒未完成的每日待办 | P |
| 日历 | 每日轻提示窗口 | life_records.py:321-341 | calendar-daily-hints.jsonl | 23:50–06:00 | 固定 | 代码写死，无入口 | 与正式提醒互斥（332） | 到点 | jsonl | 只进本轮回复末尾 | P |
| 日历 | 完成项折叠 | life_records.py:36-43 | 代码常量 | 30 | 固定 | 代码写死，无入口；页面显示折叠数（web_agent.py:2314-2317） | 只影响展示 | 读取时 | 不持久 | 无 | R |
| 日历 | 心情 | life_records.py:46-47,60-72,481-504；web_agent.py:3283-3296 | data/<角色>/life-moods.json | 空 | 每格 1 个 emoji，≤16 字符；不可未来日期 | 生活页心情槽 + 表情面板（desktop/chat.js:1122-1160） | 每人只能写自己那格（Lan 的 API 固定 'Lan'） | 点击立即 | 文件 | emoji 校验；未来日期拒绝 | U |
| 日历 | 约定识别预算 | calendar_agreements.py:60-124 | 代码常量 | 512 tokens；60 天窗口；最近 6 条；prompt 16000/8000 字符 | 固定 | 代码写死，无入口 | 仅在开关开时运行 | 每轮结束后台 | 写 calendar-agreements.jsonl 可撤销 | 失败只记错误行，不影响主回复 | A（花费） |
| 日历 | /api/life 请求体 | web_agent.py:3238-3248 | 代码常量 | 20000 字节 | 0 < len ≤ 20000 | — | 超限 400 | 每请求 | — | 防大包 | P |

---

## 3. 重复 / 冲突 / 隐性覆盖

1. **主题双源**：页面主题只读 localStorage（desktop/chat.js:2133,2140），菜单窗只读 desktop.json 的 settings.theme（desktop/main.cjs:241,244），聊天页写入时同步一份给主进程（desktop/chat.js:2160-2162），但**启动时从不回读**主进程主题。清空 localStorage/换 Electron 会话后：页面回日间、菜单仍夜间，直到用户再点一次主题。
2. **桌宠尺寸双源且主进程胜出**：localStorage 的 petSize 与 desktop.json 的 size；启动时 petSize() 的返回值覆盖 localStorage（desktop/chat.js:2170）。若两值不同（如上次崩溃未写盘），用户看到的滑块值会被静默改写——属「页面值 ≠ 实际值」。
3. **「透明度」名不副实**：整体/页面/气泡透明度只改 CSS 变量（desktop/chat.js:2141-2145，desktop/chat.css:73-76,103-106），Electron 窗口本身仍是透明底 + 半透明面板，没有调用窗口 setOpacity。管理中心若按「窗口不透明度」呈现会误导。
4. **外观数据全在 localStorage**：主题、四个透明度、Peter 尺寸、自定义 CSS、头像、侧栏、rail、最近 emoji 共 8 个键（desktop/chat.js:2125,2506,2542,2554,3050,1141）全部只在浏览器存储；不在 data/<角色>/，不进仓库、不进文件备份。换机/清缓存/改 Electron userData 路径即全丢（只有 Peter 尺寸能靠 desktop.json 恢复）。
5. **背景图与背景资料同名易混**：外观「背景图片」（userData/appearance-background.png）与人设「背景资料」（persona/peter.background.md）是两个完全不同的东西，后者才进模型（background.py:267-269）。管理中心若合并会让用户改错对象。
6. **说话样例双文件**：正文在 persona/peter.voice.md，开关在 data/<角色>/voice-style.json（voice_style.py:17,23-25,60）。删除开关文件会被解释为「开启」（62-67），不是「未设置」——清理缓存/备份恢复可能悄悄把开关打开。
7. **自动记日历默认开、删文件即回开**：calendar-settings.json 缺失 → true（life_records.py:106-111）。用户关掉后若该文件丢失（备份恢复/手删 data），约定识别会重新开始消耗模型调用。
8. **人设无上限 vs 其他资料有上限**：save_persona 只查非空（web_agent.py:623-632），而 lan.md 4000 字节（user_persona.py:34）、Learned Context 4000 字节（learned_context.py:288）、说话样例 8000 字（voice_style.py:88）都有硬上限。人设每轮都带，是目前唯一可无限膨胀的注入文本；页面只显示字节数不设限（desktop/peter-page.js:321）。
9. **[voice] 只有 provider，真正生效的是 .env**：toml 里看不出用的哪个音色/模型/区域；load_voice_config 任一缺失就整体禁用（voice_output.py:44-51），启动不报错——页面/管理中心若不显示 .env 状态，会出现「配置了却没声音」且无提示。
10. **STT 与向量嵌入共用 EMBED_API_KEY**：换嵌入 key 会同时影响语音识别（web_agent.py:941-942）；模型名写死 SenseVoiceSmall，改不了。
11. **QQ/邮件「enabled=true 但静默不生效」**：toml 两项都是 true（characters/peter/character.toml:191,198），但 .env 缺键即返回 None（qq_channel.py:225-227；email_channel.py:105-107），界面无任何状态；用户会以为已开启。
12. **死入口**：主进程实现 menu:action 'size'（desktop/main.cjs:802），但 desktop/menu.html 只有 chat/activity/appearance/quit-agent 四个按钮 → 该分支永远不可达（X）。
13. **settings.activityDetached 旧键**：启动时并入 detachedViews（desktop/main.cjs:840-844），属历史迁移键，新写入不会产生它。
14. **分页刷新频率不一致**：聊天主轮询 600 ms（desktop/chat.js:1811），生活页 15 s（2769）。同一事件（Peter 改日历）在聊天里秒级出现、生活页最多滞后 15 s。
15. **[voice]/[qq]/[email] 未在 Peter 页暴露**：Peter 页只有人设/背景/关于你/learned/关系五类（web_agent.py:600-616），渠道与语音全在文件里——这是「管理中心」最大的空白块。

---

## 4. 页面与入口现状

### 4.1 已有页面/接口（可直接改）
| 能力 | 入口 | 依据 |
|---|---|---|
| 主题、整体/分页/气泡/背景透明度、自定义 CSS、Peter 尺寸、恢复默认 | 设置页（more）「外观」卡片 | desktop/chat.html:240-259；desktop/chat.js:2139-2177,2500-2507 |
| 背景图片上传/移除 | 同上 | desktop/chat.html:252-253；desktop/main.cjs:650-681 |
| 头像 | 聊天头部头像按钮 | desktop/chat.js:3050-3069 |
| 人设 / 背景资料 / 关于你 / Learned Context / 关系底色 / 历史版本恢复 | Peter 页 | desktop/chat.html:31；desktop/peter-page.js:372-410,511；web_agent.py:3058-3068,618-683 |
| 说话样例开关与正文 | 上下文页「他平时怎么说话」 | desktop/chat.html:104；desktop/chat.js:1502-1525；web_agent.py:3195-3214 |
| 生活记录（日历/待办/事件/提醒/心情/自动记录开关） | 生活记录页 | desktop/chat.html:36；desktop/chat.js:1088-1273；web_agent.py:2275-2322,3238-3360 |
| 消息动作（删除/撤回/重生成/编辑/收藏/卡片） | 聊天消息菜单 | desktop/chat.js:631-721；web_agent.py:1637-1743 |
| 表情包上传/管理 | 聊天表情面板 | desktop/chat.js:2586-3030；web_agent.py:2208-2273 |
| 语音录制/听写/重试 | 输入框麦克风与听写按钮 | desktop/chat.js:121-141,1878-1960；web_agent.py:2624-2674,2741-2752 |
| 诊断（只读） | 诊断页 | desktop/diagnostics-page.js:19（只 GET） |

### 4.2 只能改文件（无页面）
- [voice] provider 与全部 MiniMax 键（.env）：characters/peter/character.toml:9-10；voice_output.py:44-51。
- [qq] 四项 + QQ_LAN_CONTACT：characters/peter/character.toml:195-202；qq_channel.py:23,219-228。
- [email] 三项 + 三个邮箱 env：characters/peter/character.toml:188-193；email_channel.py:29,98-108。
- 关系底色容量 max_chars/max_items/enabled：只能加 [profile.relationship_base]（characters/peter/character.toml:174-177 注释；relationship_base.py:24-44）。
- 历史轮数/输出长度、屏幕弹窗开关：characters/peter/character.toml:6-7,219-221。
- 语音识别 key/模型：.env 的 EMBED_API_KEY；模型写死（web_agent.py:941-958）。

### 4.3 代码写死、无入口
- 分泡上限 4（bubble_split.py:10）、气泡节奏 1200/2400/3600 ms（desktop/chat.js:148）、轮询 600 ms（1811）。
- 录音 120 s、听写 70 s、语音上传 15 s、10 MB（desktop/chat.js:105,109,133,1898,1907）。
- ASR 模型与端点（web_agent.py:951-958）、TTS 音频参数与 60 s（voice_output.py:99-103）。
- 窗口默认/最小尺寸、菜单尺寸、screen-pop 轮询 500 ms 与 520×270/24 px（desktop/main.cjs:102,175-179,233-236,339-341,442）。
- 提醒判定窗口 23:30/23:50、迟到 1 h 记 missed、折叠 30（life_records.py:36,254-258,296-300,321-324）。
- 心情 16 字符、记录 240/12000（life_records.py:47,174-175）。
- 表情包 10/30/60/500/2 MB/40 KB（stickers.py:24-29）。
- 日历约定模型调用 512 tokens、60 天（calendar_agreements.py:60,70,74）。

### 4.4 缺失能力（不存在的配置）
- 托盘、开机自启、系统通知/未读徽标、免打扰（除自主活动 quiet_hours 外）：无任何代码（docs/tickets/020-resident-service.md:5）。
- 移动端/远程访问：无实现，服务只绑 127.0.0.1（web_agent.py:1983-2012,3463）。
- 语音输出用户级开关（「是否用语音回复」）：只能整体禁用 [voice]/.env；页面没有切换。

---

## 5. 生效时机与持久化

| 类别 | 生效时机 | 持久化位置 | 重启保留 | 更新程序保留 | 备份/恢复 |
|---|---|---|---|---|---|
| 主题、透明度、CSS、头像、侧栏、rail | 立即（点击） | Electron userData 下 localStorage | 保留 | 保留（userData 不在仓库） | 不随 data/ 备份；清缓存丢 |
| 背景图片 | 立即 | userData/appearance-background.* + desktop.json 扩展名 | 保留 | 保留 | 不随 data/ 备份 |
| Peter 尺寸 | 松开滑块/拖角结束 | desktop.json 的 size + localStorage | 保留 | 保留 | 同上 |
| 人设/背景资料/说话样例/关于你 | 下一轮请求（保存后 reload 或每轮读文件） | characters/<角色>/persona/、data/<角色>/voice-style.json | 保留 | 保留（本机文件，不在 git） | 取决于备份是否含 characters/data |
| Learned Context / 关系底色 | 下一轮 | data/<角色>/learned-context.json + versions.jsonl | 保留 | 保留 | 随 data 备份 |
| 日历/待办/提醒/心情/自动记录开关 | 立即写入，提醒到点触发 | data/<角色>/life-*.json*、calendar-*.jsonl | 保留 | 保留 | 随 data 备份 |
| QQ/邮件/语音通道 | **重启**（启动时构建通道与 voice 实例） | character.toml + .env | 保留 | 保留（.env 不在 git） | 随本机文件；换机需重填 .env |
| 消息编辑/删除/收藏/卡片 | 立即 | 聊天记录 | 保留 | 保留 | 随 data 备份 |
| 表情包库 | 立即 | 可写根的 files/ + index.json | 保留 | 保留 | 随 data 备份 |
| 窗口尺寸/分离视图状态 | 关闭/移动后 180 ms 防抖 | desktop.json | 保留 | 保留 | 不随 data 备份 |

结论：**「外观与窗口」只在本机 Electron 存储**，重装/清 userData/换机会丢；**「人格与日历/渠道」在 characters/、data/ 与 .env 中**，是否保留取决于备份范围（备份实现未核对，见第 7 节）。

---

## 6. 风险与建议分类

### S · 敏感权限（需授权/审批，不能只靠页面）
- MiniMax 与 EMBED_API_KEY：明文 .env，页面展示必须脱敏；任何「测试连接」都不应回显密钥（voice_output.py:1,44-51；web_agent.py:939-944）。
- QQ 通道会用 UI Automation 抢焦点、写剪贴板、模拟按键（qq_channel.py:426-468），属高权限桌面操作；管理中心只能管开关与上限，不应在页面里「代发」。
- 邮件可外发（收件人固定 Lan，正文 ≤4000，日上限 20），改 daily_max_sent 应视为敏感变更（email_channel.py:185-186）。
- screen_pop 会全屏置顶打扰（desktop/main.cjs:263-265），开关应由用户显式控制（characters/peter/character.toml:219-221）。

### P · 程序安全约束（保持写死，不开放给用户改）
- 麦克风权限白名单（desktop/main.cjs:20-32）。
- 回环绑定 + Host/Origin 校验（web_agent.py:1983-2012,3463）。
- 自定义 CSS 黑名单（desktop/chat.js:2500-2502）、CSP（desktop/chat.html:6）。
- 分泡上限、录音/上传上限、ASR 端点与模型、TTS 响应上限、screen-pop 字段校验、表情包/卡片/记录字段校验、提醒去重。
- 单实例锁（desktop/main.cjs:826）。

### U · 用户常用（可页面直接改）
- 主题、四种透明度、Peter 尺寸、背景图片、头像、侧栏/rail。
- 说话样例开关与正文、关于你（4000 字节内）、Learned Context 各段（共享 4000）、关系条目。
- 生活页全部：记录/待办/提醒提前量/心情/自动记录开关。

### A · 高级（需说明影响）
- 自定义 CSS（可破坏布局、隐藏按钮；已有 20k 与注入黑名单）。
- 人设无上限（建议加软上限提示，而不是硬删）。
- 关系底色容量 max_chars/max_items（影响每轮请求的固定开销）。
- 渠道 daily_max_sent/poll_seconds/idle_seconds（影响打扰频率与花费）。
- 日历约定识别（每次触发一次模型调用，512 tokens 上限；分类账 calendar-agreements）。
- voice 计费率（写错会直接影响预算账）。

### R · 只读运行信息
- 版本列表、折叠数、提醒 missed 标记、transcribing_voice 状态、诊断页。
- localStorage 的最近 emoji/听写草稿属便利缓存，不需要管理。

### X · 冗余/废弃（给清理依据）
- menu:action 'size' 分支不可达（desktop/main.cjs:802 对比 desktop/menu.html）。
- settings.activityDetached 旧迁移键（desktop/main.cjs:840-844）。
- veil-life-recent-emoji 纯界面缓存（desktop/chat.js:1141）。
- [voice] 仅剩 provider，若管理中心统一收口可考虑并入 .env 状态展示；[qq]/[email] 的 enabled=true 与 env 缺失造成的「假开启」应显式呈现。
- 托盘/开机自启：不是废弃，而是**未实现**，管理中心不应画开关。

---

## 7. 无法确认项（含原因）

1. **备份/恢复是否包含 characters/、.env、Electron userData**：本次只读域 7 源码，未核对 backup_restore.py 的清单，因此第 5 节「备份后是否保留」只能按目录推断，不能断定。
2. **split_bubbles 是否总以默认 4 调用**：未逐一核对所有调用点是否传自定义 limit；表内只能保证默认值与函数行为。
3. **前端「最近 emoji」面板最多显示多少项**：veil-life-recent-emoji 存 16 个（desktop/chat.js:1141-1142），但面板渲染是否有额外截断未核对。
4. **自定义 CSS 是否会被既有 !important 规则覆盖**：chat.html:255 与 chat.js:2502 两处限制一致，但 chat.css/desktop-redesign.css 中大量 !important 可能导致用户样式部分无效，未逐条核对。
5. **petSize 与 desktop.json size 冲突时用户可见的最终值**：代码上启动时主进程值覆盖 localStorage（desktop/chat.js:2170），但若主进程读 desktop.json 失败回退 320，会同时覆盖用户上次设置；丢失概率取决于写盘时序，未实测。
6. **QQ 通道在非 Win32 上是否另有用户提示**：from_env 直接返回 None（qq_channel.py:221），主程序是否另行提示未核对。
7. **[voice] 配置存在但 .env 缺失时，页面是否给任何提示**：代码上 make_voice 返回 None、send_message 不接受 voice（veil_agent.py:2952），未见用户可见提示，判定为「静默禁用」，未在运行实例验证。
8. **menu.html 是否由运行时注入额外按钮**：静态文件只有四个按钮，未见动态注入（desktop/menu.js:2），故判 size 为死分支；若有打包期替换则需复核。
9. **移动端是否另有仓库/独立入口**：仓库内无实现；产品文档提到「手机版 VEIL」是另一个产品，未展开核对其入口。
10. **Electron localStorage 的实际落盘文件名**：sessionData 指向 userData/session-data（desktop/main.cjs:37），但具体 Local Storage leveldb 路径未核对，只确认随 userData 走。
