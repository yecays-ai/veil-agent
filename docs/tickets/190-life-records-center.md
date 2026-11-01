# 工单 190：Peter 与 Lan 的生活记录中心

## 目标
Lan 在 Windows 桌面全屏查看日历、便签、计划、日记及完成历史，并与 Peter 共同维护生活记录。

## 当前状态
分支 `ticket/190-life-records-center` 从本地 main `d4a868c` 建立。现有 Peter 长期任务在 `long_tasks.py` 的 `data/peter/long-task.json`；聊天短期便签在 `short_term_notes.py` 的 `data/peter/notes.jsonl`；Ombre 计划由 `plan` 和 `trace` 维护。自主唤醒由 `autonomy.py` 提供 `set_next_wake`。页面沿用 Electron `desktop/chat.html`、`chat.js` 与本机 `web_agent.py`，生活记录使用角色本地文件，不自动写入 Ombre。

## 验收步骤
1. Windows 全屏打开「生活」，在日历选择日期，创建共同事件和 Lan 日记，刷新及重启后确认记录仍在。
2. 请 Peter 用正式工具添加便签和日记，再在页面查看；完成一项后去「已完成」查看并确认作者、修改时间与历史。
3. 打开长期计划，确认 Peter 原有长期任务显示；正常聊天、Ombre 和记忆页仍可使用。
4. 在生活页直接编辑 Peter 原有长期任务及短期便签，刷新后确认原数据源中的内容和修改者仍正确；完成长期任务时填写可核实的结果。
