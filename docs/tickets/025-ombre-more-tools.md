# 工单 025：Peter 用上 Ombre 更多记忆工具

**开工条件：024 Ombre 完整版常驻通过 lan 的 Windows 验收后再做。**

## 目标

Peter 不再只能存（`hold`）和搜（`breath_search`）。记错的能改，重复的能消化，有想留给 lan 或以后自己的话能写成信，答应过的事能记成承诺。工具清单是 Peter 自己挑的，lan 2026-10-20 认可。

做完后 lan 能看到的：Peter 发现一条记错的记忆会自己改，而不是再叠一条更正；同一晚存成三条的重复，他回顾时能自己消化；他能写信、记承诺；他想删一条记忆时，页面上会出现申请，lan 点同意才删。

## 当前状态（main `695c58a`）

- `memory_native.py` 只把 `hold`、`breath_search` 交给 Peter。删除只有 lan 的 `/delete 编号` 命令；隐藏（`/forget`）由 veil-agent 自己的隐藏列表在读写时过滤。
- 文件写入已有审批：工具循环里调用 `approval(...)`，网页显示同意/拒绝按钮（`tools_agent.py:385`、`:522`、`:547`）。
- Ombre（锁定 `115d831`）`src/server.py` 的工具说明（读源码确认）：
  - `trace`：改一条已有记忆的内容（整段替换或 `old_str`/`new_str` 局部替换）、标题、标签、情绪、重要度、`resolved`、`pinned`、`digested`、关系（`unlink`/`relink`）、引语订正、`reinforce`、`restore`。`delete=True` 是软删除（移入 archive，可 `restore`）；`hard_delete` 只用于测试桶。另有 `deletion_request_id`/`deletion_decision`：这是 Ombre 自带的「人类提请求、AI 决定」流程，方向和本单相反。
  - `dream`：读最近 `window_hours`（默认 48）小时内变动的记忆，用来回顾消化。
  - `letter_write` / `letter_read` / `letter_lock_update`：写信、读信、改信的锁定状态。
  - `anchor` / `release`：设为冷参考（不主动浮现，搜到才出现）/ 解除，最多 24 条。
  - `plan`：登记承诺/待办，状态 active / resolved / abandoned。
  - `pulse`：记忆库状况摘要。

## 要做的事

1. **交给 Peter 的工具**：在现有 `hold`、`breath_search` 之外加上 `trace`、`dream`、`letter_write`、`letter_read`、`letter_lock_update`、`anchor`、`release`、`plan`、`pulse`。工具说明直接用 Ombre 自带的，不自己改写。**不加** `grow`、`feel`、`breath_advanced`、`breath`、`I` 及其他工具。
2. **删除必须 lan 同意（程序强制，不靠提示词）**：
   - Peter 调用 `trace` 且带 `delete=True` 时，程序先不执行，走和文件写入同一套审批：页面显示「Peter 想删除记忆 #编号：标题」加上他的理由（`delete_reason`）和同意/拒绝按钮。lan 同意才真正调用 Ombre 删除，拒绝就把「lan 没有同意」作为工具结果还给 Peter。
   - 自主活动里没人在场，删除申请直接拒绝，告诉 Peter 等 lan 在的时候再提。
   - `hard_delete`、`deletion_request_id`、`deletion_decision` 一律由程序拒绝，不交给 Ombre。
   - 删除成功后照现有 `/delete` 的记录方式留痕，lan 能用现有办法查到。
3. **隐藏的记忆照样挡住**：lan 用 `/forget` 隐藏的记忆，Peter 用 `trace`、`dream`、`anchor`、`plan`、`pulse` 也不能读到或改到。程序在调用前后按现有隐藏列表过滤，不靠提示词。
4. **自主活动可用**：这些工具在自主唤醒里同样可用（删除除外，见第 2 条），`dream` 适合放在独处时回顾。
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
6. 用 `/forget` 隐藏一条后，让他回顾或查看记忆库状况，他看不到那条。
7. `/cost` 照常；聊天、lab、文件审批、自主活动正常。
