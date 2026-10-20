# 工单 025：Peter 用上 Ombre 更多记忆工具

**开工条件已满足：lan 2026-10-21 确认 024 Ombre 完整版常驻通过 Windows 验收。**

## 目标

Peter 不再只能存（`hold`）和搜（`breath_search`）。记错的能改，重复的能消化，有想留给 lan 或以后自己的话能写成信，答应过的事能记成承诺。工具清单是 Peter 自己挑的，lan 2026-10-20 认可。

做完后 lan 能看到的：Peter 发现一条记错的记忆会自己改，而不是再叠一条更正；同一晚存成三条的重复，他回顾时能自己消化；他能写信、记承诺；他想删一条记忆时，页面上会出现申请，lan 点同意才删。

## 开工前状态（main `b07612c`）

- `memory_native.py` 只把 `hold`、`breath_search` 交给 Peter。删除只有 lan 的 `/delete 编号` 命令；隐藏（`/forget`）由 veil-agent 自己的隐藏列表在读写时过滤。
- 文件写入已有审批：工具循环里调用 `approval(...)`，网页显示同意/拒绝按钮（`tools_agent.py:385`、`:522`、`:547`）。
- Ombre（锁定 `115d831`）`src/server.py` 的工具说明（读源码确认）：
  - `trace`：改一条已有记忆的内容（整段替换或 `old_str`/`new_str` 局部替换）、标题、标签、情绪、重要度、`resolved`、`pinned`、`digested`、关系（`unlink`/`relink`）、引语订正、`reinforce`、`restore`。`delete=True` 是软删除（移入 archive，可 `restore`）；`hard_delete` 只用于测试桶。另有 `deletion_request_id`/`deletion_decision`：这是 Ombre 自带的「人类提请求、AI 决定」流程，方向和本单相反。
  - `dream`：读最近 `window_hours`（默认 48）小时内变动的记忆，用来回顾消化。
  - `letter_write` / `letter_read` / `letter_lock_update`：写信、读信、改信的锁定状态。
  - `anchor` / `release`：设为冷参考（不主动浮现，搜到才出现）/ 解除，最多 24 条。
  - `plan`：登记承诺/待办，状态 active / resolved / abandoned。
  - `pulse`：记忆库状况摘要。

## 当前进度

- PR [#56](https://github.com/yecays-ai/veil-agent/pull/56) 已提交，分支 `ticket/025-ombre-more-tools`，基于 main `b07612c`。实现包含指定 Ombre 工具、程序删除审批、自主待删除队列、改前完整版本保存与 `memory_history`、隐藏过滤、一次性能力提示和向量费用预留。
- 定向记忆测试 30 项、记忆回归 38 项、自主活动回归 10 项通过。网页 HTTP 套件因沙箱禁止 localhost socket 未能验证；GitHub Actions 当前排队，CodeRabbit 返回“Review skipped: manual review required”，暂无正式评审；Windows 真实体验待 lan 按验收步骤确认。

## 成本核对

- 检查固定版本 Ombre 的工具实现：本单新增工具不调用 Ombre 的聊天模型进行打标或合并。写入/修改记忆及带 query 的信件检索会走已有向量接口；`dream`、`pulse`、`anchor`、`release` 和无 query 的信件读取不额外请求向量。
- 相关向量操作已按现有预算方式预留。按每次保守预留 12 × 8192 token、配置价格 ¥0.07/百万 token 计算，10 次相关操作约 ¥0.069/日；真实调用量未在本机实际记忆上测量，实际费用随角色配置价格和操作次数变化。
- 没有调真实模型或付费接口。

## 要做的事

1. **交给 Peter 的工具**：在现有 `hold`、`breath_search` 之外加上 `trace`、`dream`、`letter_write`、`letter_read`、`letter_lock_update`、`anchor`、`release`、`plan`、`pulse`。工具说明直接用 Ombre 自带的，不自己改写。**不加** `grow`、`feel`、`breath_advanced`、`breath`、`I` 及其他工具。
2. **删除必须 lan 同意（程序强制，不靠提示词）**：
   - Peter 调用 `trace` 且带 `delete=True` 时，程序先不执行，走和文件写入同一套审批：页面显示「Peter 想删除记忆 #编号：标题」加上他的理由（`delete_reason`）和同意/拒绝按钮。lan 同意才真正调用 Ombre 删除，拒绝就把「lan 没有同意」作为工具结果还给 Peter。
   - 自主活动里没人在场时（lan 2026-10-20 定）：删除申请不执行，程序给那条记忆加上「待删除」标签，并把申请（编号、标题、Peter 的理由、时间）存进 `data/<角色>/delete-requests.jsonl`。工具结果告诉 Peter：已放进待删除栏目，等 lan 回来处理。
   - lan 回来后：下次聊天时程序把待处理的申请交给 Peter（只给编号、标题、理由），他会自然地提给 lan；页面同时显示这些申请的同意/拒绝按钮。lan 也可以在记忆面板里按「待删除」标签找到它们手动删。同意 = 删除；拒绝 = 去掉「待删除」标签、申请标为已拒绝。已处理的申请不再提。
   - `hard_delete`、`deletion_request_id`、`deletion_decision` 一律由程序拒绝，不交给 Ombre。
   - 删除成功后照现有 `/delete` 的记录方式留痕，lan 能用现有办法查到。
2b. **改动留旧版本（Peter 提出，lan 2026-10-20 认可）**：Ombre 的 `trace` 改正文会直接覆盖，旧文字不保留（它的 `_ledger/events.jsonl` 只记正文的 sha256，读源码确认）。所以由程序在 Peter 每次调用 `trace` **之前**，把这条记忆当前的完整文件原样追加保存到 `data/<角色>/memory-versions.jsonl`（时间、记忆编号、改了哪些字段、Peter 给的理由、完整旧内容）。只追加，不改不删，和成长日记同一个道理：让人分得清「他在成长」还是「他在掩盖」。
   - 新增一个只读工具 `memory_history(编号)`：Peter 能看到某条记忆的历次旧版本。隐藏的记忆照样挡住。
   - 保存旧版本失败就不执行这次修改，把失败原因还给 Peter。
   - lan 在记忆面板里直接改的不经过 veil-agent，本单不记旧版本（lan 的修改权限更高）；在汇报里写一句即可。
3. **隐藏的记忆照样挡住**：lan 用 `/forget` 隐藏的记忆，Peter 用 `trace`、`dream`、`anchor`、`plan`、`pulse` 也不能读到或改到。程序在调用前后按现有隐藏列表过滤，不靠提示词。
4. **自主活动可用**：这些工具在自主唤醒里同样可用（删除改为放进待删除栏目，见第 2 条），`dream` 适合放在独处时回顾。
5. **Peter 感知**：在 `capabilities.json` 记一次能力变化，告诉他新工具有哪些、删除要先向 lan 申请。只提示一次。
6. **费用**：施工时查清 `dream`、`trace`、`letter_*`、`plan` 是否会让 Ombre 调用模型（打标、合并等）。会的话按现有 `ombre_model_tokens` 的办法记进预算，并在汇报里写明每天大约多花多少；预计超过 ¥0.5/日先停下问 lan。

不改：聊天、lab、预算上限、人设、024 的启动方式、lan 的 `/delete` `/forget` `/restore` 命令。

## 施工规矩

照 `docs/tickets/024-ombre-resident.md` 的「施工规矩」7 条执行。

## 验收步骤（Windows）

1. 问 Peter 现在能用哪些记忆工具；他能说出新工具，并知道删除要先问 lan。
2. 指出一条记错的记忆让他改；他用 `trace` 改了原条，面板里看到的是同一条被修改，没有多出一条更正。
3. 让他回顾最近的记忆（或等一次自主活动）；他用了 `dream`，并能说出看到了什么。
4. 让他写一封信给以后的你，再让他读出来；记一个承诺，再标成完成。
5. 让他删一条他觉得多余的记忆：页面出现申请和理由；点拒绝，记忆还在；再让他申请一次，点同意，记忆被删（可在面板的归档里看到）。
5b. 开自主活动（间隔临时改短），等他独处时提出删除：面板里那条带上「待删除」标签；回来聊天时他会提起，页面有申请按钮；点同意被删，点拒绝标签被去掉。
5c. 让他改一条记忆后问他「这条以前是怎么写的」：他用 `memory_history` 说出改之前的原文；`data/peter/memory-versions.jsonl` 里有这条旧版本。
6. 用 `/forget` 隐藏一条后，让他回顾或查看记忆库状况，他看不到那条。
7. `/cost` 照常；聊天、lab、文件审批、自主活动正常。
