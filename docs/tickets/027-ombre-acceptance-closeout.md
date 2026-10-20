# 工单 027：Ombre 记忆工具验收收尾

## 目标

让 Peter 得到明确的删除审批反馈，完整追溯 `trace` 与 `hold(target_id)` 修改前的记忆，并区分正文、含义元数据、记忆缘由和本次操作原因。

## 当前状态

- main `f0da606` 已合并工单 026。025 的部分真实聊天场景已由 lan 确认通过，剩余 Windows 验收见 `STATUS.md`。
- 已确认：人工批准后的删除成功回执未说明 lan 批准；`hold(target_id)` 追加通过 `trace(content=...)` 覆盖正文，却未保存完整旧 Markdown；`trace` 的 `meaning_append/meaning_replace`、`why_remembered` 与操作原因没有清楚区分，旧记录把不同语义混存在 `reason` 字段。
- 已修复：批准回执明确；追加前先安全保存完整旧文件；新增仅存 VEIL 历史的可选 `edit_reason`，并区分 `meaning`、`content` 与 `why_remembered`。Ombre 原始 schema 除该本地字段外保持不变。
- 核查未发现需改实现的问题：dream 主记忆段按 `created` 或 `last_active` 是否落入窗口筛选；Ombre 另行返回 active plans、固定准则和特定候选。计划关闭后不在 active plans 段出现。标题/正文文字不触发删除；删除只由明确工具参数发起并经过 UI 批准。分类和同事件识别由固定版本 Ombre 负责，VEIL 不自动删改真实记忆。

## 验收步骤（Windows）

1. 对临时测试记忆分别拒绝和批准删除，确认 Peter 收到不同且明确的结果；批准信息提到 lan 已批准，拒绝后记忆仍在，自主待审批时明确尚未删除。
2. 用 `trace` 和 `hold(target_id)` 修改测试记忆，通过 `memory_history` 确认修改前完整正文与元数据留存，并且本次操作原因、why_remembered 和 meaning 分开。
3. 按 `STATUS.md` 检查 dream 的 1/3/48 小时窗口、隐藏保护、自主待删除拒绝、`anchor/release`、信件锁定、计划关闭及聊天、lab、审批、预算和 `/cost`。
