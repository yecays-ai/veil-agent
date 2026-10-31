# 工单 178：清理确认的死代码

## 目标

- 只删除生产代码和测试都不用的东西，不改变任何行为。
- 这是仓库整理期的第一项，之后依次是无用 import 与重复实现、`make_handler`、`Hub`、`_tool_chat` 的结构拆分，每项一个 PR。

## 做了什么

- `autonomy.py`：删除常量 `GLANCE_HISTORY_LIMIT`、单数版 `_transition_alarm`（只剩 `_transition_alarms` 在用）、没人读的 `delivered_path`。
- `veil_agent.py`：删除单数版 `delete_message`（只有 `delete_messages` 被网页调用）。
- `profile_notes.py`：删除常量 `PROFILE_TOOLS`。
- `desktop_icons.py`：删除没人调用的两个 `is_auto_arrange`。
- 局部变量：`windows_interaction.py` 的 `stroke_idx`、`windows_discovery.py` 的 `refusal_reason`（改为 `_reason`）、`memory_agent.py` 的 `terminal`、`backup_restore.py` 的 `personas`。

## 有意没删

- 只被测试用的六个函数（`_read_alarm`、`_save_alarm`、`get_latest_snapshot`、`begin_tool`、`collapse_latest_photo`、`pending_media_info`）：可能是历史接口，也可能是应接入的功能，需要逐个判断。
- `ombre_entry.py` 里给外部库打补丁的四处赋值、`announce_email`/`announce_qq`（动态调用）、HTTP 处理器方法、`cbSize`、`UPSTREAM_REPOSITORY`（记录上游地址）。

## 验收

- 不需要新的手动验收：没有行为变化。更新后正常聊天、自主活动、桌面整理各用一次即可。
- 自动测试：全套结果与修改前一致（失败项均为沙箱环境限制，CI 为准）。
