# 工单 133 自主活动和能力说明一次整理（一个 PR）

## 目标
一个 PR 做完下面六部分：Peter 知道今天还能醒几次；夜里 1–6 点可开关的安静时段；醒来时的批准结果说真话；每轮固定事实不再过时；他能改写自己的作品、扔进自己的垃圾桶（不能删除）；醒来不再被长度上限截断，自主预算 ¥3/日。

Lan 2026-10-27 确认了全部内容，并要求合成一个 PR，由 Claude 实现。

实现要点：程序文件 `autonomy.py`、`veil_agent.py`、`tools_agent.py`、`desktop_files.py`、`windows_interaction.py`、`codex_watch.py`、`agent_types.py`、`web_agent.py`，桌面 `desktop/chat.html`、`chat.js`、`main.cjs`；测试 `tests/test_autonomy_cleanup.py`。桌面文件改动后 update.bat 会重新打包 exe。在本工单之前建的桌面文件不在台账里，Peter 只能改写/挪走本工单之后新建的。

## 第 1 部分：Peter 看得到今天还能醒几次
### 目标
Peter 醒来时和聊天时都知道今天已经醒了几次、还剩几次、什么时候重置，自己约的醒来落空时也知道原因。

### 现状
- 醒来说明（`autonomy.py` `AutonomyController._context`）只给自主预算的钱数；每轮账本（`veil_agent.py` `Budget.display`）也只给钱数。每天最多 12 次只在工单 118 上线时通知过一次。
- `set_next_wake` 成功时不提次数；到点因为当天次数已满，闹钟在 `due()` / `claim_due()` 里被静默改成 `missed`，Peter 不知道。

### 改动
- 醒来说明预算那一行后加一句事实：`Autonomous wakes today: n of 12 used; the count resets at local midnight.`（数字取 `_eligible_count()` 和 `daily_max_runs`）。
- 每轮账本里「自主 ¥x / 3/日」后面加「，已醒 n/12 次」。
- `set_next_wake` 成功时返回当天剩余次数；约的时间如果当天次数已满、或落在夜间安静时段（第 2 部分），直接在返回里说明这次会不会醒。
- 闹钟被标成 `missed` 时，下一次醒来说明里给一句：哪一次约的醒来没发生、原因是次数已满。
- 不加任何能力清单，只是状态数字。

## 第 2 部分：夜间安静时段（可开关）
### 目标
每天 01:00–06:00（本机时间）Peter 不自主醒来，次数留到白天用；Lan 想晚上看他活动时，在桌面 App 里关掉这个开关就行。

### 现状
`autonomy.py` 定时醒来只看间隔（90 分钟）和当天次数（12），不看时间段。Lan 本机 `data/peter/autonomy.jsonl`：2026-10-27 的 12 次里有 6 次在 01:06–08:36，17:30 后当天就没有了。

### 改动
- `character.toml` `[autonomy]` 加 `quiet_hours = "01:00-06:00"`，`quiet_hours_enabled = true`（默认开）。
- 开关状态存 `data/peter/autonomy-quiet.json`，桌面 App 自主活动开关旁边加「夜间安静 1–6 点」开关，重启后保持；需要把对应接口加进 `desktop/main.cjs` 请求白名单（同工单 124 的教训）。
- 开着时：安静时段内不触发定时醒来，也不触发 `set_next_wake` 约的醒来（约在安静时段的，`set_next_wake` 直接返回「这个时间在夜间安静时段，不会醒」）；06:00 后照常按间隔醒。安静时段没发生的醒来不算次数。
- 关掉时：和现在一样。
- 开关变化时记一条能力记录（`record_capability_change`），一次性通知 Peter，之后他用 capability_history 查。

## 第 3 部分：自主醒来时，需要批准的操作如实告诉 Peter
### 目标
Peter 自己醒来时碰到需要 Lan 批准的操作，程序告诉他真实情况：Lan 这次没有被问到；而不是说「Lan 没同意」。

### 现状
- `AutonomyController.run` 把批准函数换成永远回答「否」（`self.chat.approval = lambda *_: 'n'`），Lan 根本没被问到。返回给 Peter 的却是 `Lan did not approve this write…`（`tools_agent.py`）或 `Lan did not approve this action…`（`windows_interaction.py`）。
- 需要 Lan 批准的操作只有三类：删除记忆（醒来时已经会挂起，等 Lan 在记忆页处理，这条不用改）；在白名单软件里点保存/发送/提交/发布/覆盖类按钮；在实验室外的 workspace 里新建文件或追加内容（成长日记有长期授权的除外）。
- 瞄一眼和 Codex 提醒也走同样的自动拒绝。

### 改动
- 醒来、瞄一眼、Codex 提醒期间的批准函数换成一个明确的「不在场」结果；后两类操作返回：`Lan is not asked during your own autonomous activity, so this was not done. It needs her approval: ask her later in chat or on QQ.`
- 正常聊天时 Lan 真的点了「否」，文字保持「Lan did not approve」。
- 删除记忆的挂起流程不变。

## 第 4 部分：每轮固定事实里的过时句子
### 目标
Peter 每轮都会读到的固定事实和他现在的真实情况一致，不再和一次性能力通知互相打架。

### 现状
`veil_agent.py` `self_facts()` 每轮发的 SYSTEM FACTS 里：
- `Available: text chat (web page or terminal)`：没有桌面 App、QQ、邮件、照片、语音输入。
- `the user can list and delete memories with /memory and /delete`：Lan 现在用记忆页（工单 132）查看、修改、删除。
- 结尾 `Unlisted abilities are unavailable.`：和上面不完整的一句放在一起，等于说 QQ、照片这些他没有。

### 改动
- 聊天方式改成如实一句：Lan 在 VEIL 桌面 App 里和他聊，也可能通过 QQ 或邮件；来自 QQ/邮件的消息有来源标记。只写当前配置里真正开着的渠道（看 `away_channels` / 配置），不列工具。
- 记忆那句改成：Lan 在记忆页里查看、修改和删除记忆。
- `Unlisted abilities are unavailable.` 改成：`Your callable tools are the ones defined for this request; use capability_history for the dated record of changes. Do not invent memories, actions or tool results.`
- 不加能力清单，不加风格提示。

## 第 5 部分：Peter 可以修改自己的作品，扔进自己的垃圾桶
### 目标
Peter 可以直接改写他自己做的东西（实验室文件、他在 Lan 桌面上新建的文件），不用再另存一份；不想要的可以挪进 `lab/trash/`。永久删除仍然不行，Lan 的文件仍然一个都不能动。

### 现状
- 实验室（`tools_agent.py` lab 规则）：只能新建和追加，不能覆盖、原地改、删除；只能在普通文件夹和 `lab/archive/` 之间移动单个文件。
- `desktop_create_file`（`desktop_files.py`）：只能新建，不能改他自己建过的文件。
- 每轮的实验室说明（`FilesystemTools._description`）是一大段操作规程（plan B、v1/v2/v3、先完整读旧版、核对字节数）。

### 改动
- 实验室内允许覆盖和编辑已有文件。每次覆盖前，程序自动把旧版本存一份到 `data/peter/lab-versions/`（Peter 看不到、不能删），Lan 需要时能找回。
- `desktop_create_file` 建的文件记进一个程序台账（路径 + 创建时的文件身份）；只有台账里、且身份没变（Lan 没改过、没替换过）的文件，Peter 可以覆盖，覆盖前同样存旧版本。台账外的文件照旧不能动。
- 新增 `lab/trash/`：`move_lab_file` 可以把实验室文件挪进去；台账里他自己的桌面文件也可以挪进 `lab/trash/`。不提供任何删除。
- 实验室说明缩成事实：能新建、改写、追加、在实验室里挪文件、扔进 trash；不能删除、不能执行脚本；改写前程序会留旧版本。去掉 plan B 等规程要求。
- 能力变化发一次性通知，记进能力记录。

## 第 6 部分：醒来不再被长度上限截断；自主预算 ¥3/日
### 目标
Peter 醒来做事不会读几个文件就被程序截断；自主活动每天可以花到 ¥3。

### 现状
- Lan 本机记录：2026-10-26 12:00 到 10-28 00:00 的 19 次醒来里，7 次因为 `The tool call history exceeded its size limit` 中途停止。原因是 Peter 的 `max_input_tokens = 32768`（`characters/peter/character.toml`），醒来时带着实验室最近文件原文，再读几个文件就超。
- 自主预算 `daily_budget_cny = "2"`；10-27 全天自主实际花了约 ¥1.3。

### 改动
- `[autonomy]` 加 `max_input_tokens = 65536`，只在醒来、瞄一眼、Codex 提醒的请求里用；正常聊天仍是 32768（聊天花费不变）。确认 Peter 当前模型支持 64k 输入（DeepSeek 官方上下文长度）。
- `[autonomy]` `daily_budget_cny` 改成 `"3"`；确认程序总预算（¥10/日、¥300/月）不受影响。
- 每轮账本和醒来说明里的数字跟着变（第 1 部分的那一行）。
- 发一次性能力通知：醒来可用的上下文变大、自主预算变成 ¥3/日。

### 花费
自主有 ¥3/日硬上限，所以每天最多比现在多 ¥1.7（10-27 实际 ¥1.3）。正常聊天不变。

## 验收（update.bat 后）
1. Peter 看得到今天还能醒几次：update.bat 后等他醒一次，在聊天里问「你今天还能醒几次」，他的回答和活动面板里的次数一致。
2. 夜间安静时段（可开关）：1. 开关开着过一夜，第二天看活动面板：01:00–06:00 没有醒来记录，下午 5 点后还有次数。 2. 晚上把开关关掉，01:00 后 90 分钟内他会醒一次。
3. 自主醒来时，需要批准的操作如实告诉 Peter：让他醒来时尝试在记事本里点保存（或看活动记录里一次被拦下的保存），他之后说的是「醒着的时候不会问你」，不再说「你没同意」。
4. 每轮固定事实里的过时句子：问他「你能在 QQ 上找我吗」「我在哪看你的记忆」，回答和实际一致。
5. Peter 可以修改自己的作品，扔进自己的垃圾桶：1. 让他把实验室里一个旧作品改一改，他直接改写同一个文件，不再生成 v2。 2. 让他清理桌面上他自己做的东西，那些文件出现在 `lab/trash/`，桌面上 Lan 自己的文件一个没动。
6. 醒来不再被长度上限截断；自主预算 ¥3/日：update.bat 后过一天看活动面板：醒来记录里不再出现「工具调用记录超过长度上限」；自主花费显示 `/3/日`。
