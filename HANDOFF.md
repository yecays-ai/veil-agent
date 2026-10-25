# 交接

## 当前进度

- 工单 110：Lan 电脑上 Peter 的长期任务卡死。根因：自主活动中新建记忆（`hold` 无 `target_id`）建立的检查点目标是记忆标题，没有任何读取工具的目标能与之匹配，所以永远无法核实；而标记 blocked 也不会清除待核实操作，`before_tool` 在自主活动中继续拦截所有副作用操作，形成死锁。修复：`memory_native` 新建记忆结果附 `memory_id`，`finish_tool` 把检查点目标改绑到该 ID（`memory_read` 可核实）；`long_task_update status=blocked` 清除未核实操作并记「无法核实，已阻塞」；拦截提示按工具类型指明读取工具。
- Lan 机器上已卡住的那条（目标是标题 `Growth journal: first entry written unpr`）无法用读取核实，需 Peter 自己设 blocked 放行；提示已写明。
- PR #154（工单 101）已合并为 `d00327c`。
- PR #155（工单 110）已合并为 `e0c532a`。
- 工单 111「桌面观察能力」：分支 `ticket/111-desktop-observation` 基于最新 main `e0c532a` 开发。已接入 `windows_list`、`windows_foreground`、`windows_observe`，复用 `uiautomation==2.0.29`、现有图片请求、工具循环、角色权限、预算和 `tools.jsonl` 活动账本；聊天与自主活动共用工具链，QQ 桌面工作与观察共用互斥锁。Lan 的 Windows 实机体验尚未进行。

## 下一步

1. 工单 110 的 PR #155 已合并；Lan 按 `docs/tickets/110-long-task-unstick.md` 验收，Windows 体验仍待确认。
2. 工单 111 的桌面观察 PR 等待审查与 CI；保持打开，待 Lan 完成 Windows 实机验收，不自动合并。
3. 其他没有读取路径的副作用工具（`plan`、`letter_write`、`letter_lock_update`、`grant_append`）检查点目标同样可能无法匹配；现在可以靠 blocked 放行，若 Peter 常卡在这些工具上，再单开工单绑定可读取的目标。

## 此前工单当前状态

合并和 Lan 体验验收分别记录在 `STATUS.md`。已验收的 095、096、102、103–105 不再列为待验收。仍需观察/验收：097 持续观察；098 等待 2026-11-07 邮箱开通；099 网页读取及其他场景；100 定时唤醒；101 附件体验；106 真实窗口操作；107 QQ 表情整句发送；108 Windows 一键更新；109 普通故障恢复提示；110 长期任务验收；111 Windows 桌面观察。

095 的 UI 视觉设计改版是独立后续事项。本次只增加附件卡片交互，不改整体 UI 视觉设计，也不修 QQ 表情问题。

## 重要边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 保持在 `main`；其中预存的两个脚本删除状态来自用户环境，未纳入本分支。当前任务只在独立 worktree 开发。
- 不覆盖角色数据；损坏或无法安全更新的闹钟状态停止并提示保留 data。闹钟只是一次自主活动触发，不扩大工具权限，也不保证模型一定选择发送消息。为避免重复，若进程在闹钟已消费后、活动开始标记写入前崩溃，该次唤醒可能跳过；服务在到期前重启或电脑睡眠/关机后恢复时，仍按持久状态处理。
- Windows 睡眠期间不能准点运行；恢复后服务在下次空闲轮询时至多补醒一次。电脑需恢复运行且 VEIL 服务启动，Lan 的 QQ 联系方式也需已配置并可用。

## 技术要点（长期有效，改写本文件时请保留）

- QQ（102–107）：不注入、不用协议；只在 Lan 两分钟没碰电脑且前台不是全屏程序时操作窗口，打字前核对聊天窗口顶部是 Lan。QQ 界面没有类名，`parse_chat` 按名字解析（常量在 `qq_channel.py` 顶部）；QQ 升级后读不到时，先在 Lan 电脑上跑 `.local-tmp\qq_check2.py` 打印结构。写剪贴板必须用 `qq_channel.set_clipboard_text`（按 UTF-16 长度）：uiautomation 2.0.29 的 `SetClipboardText` 遇到表情会截掉句尾，不要换回去。缺 uiautomation 时抛 `QQUnavailable` 提示重跑 setup.bat。授权码和 Lan 的 QQ 名字只放在本机 `.env`。
- 桌面活动窗口（106）：`Hub.tool_activity` 按 tools.jsonl 的大小/修改时间缓存，并在 Hub 锁外计算；每个窗口每 600 ms 轮询一次，不要改回每次都重读。
- 更新（108）：`update.bat` 只执行 `git pull --ff-only origin main`，不 stash、不切分支，出问题就停下说明原因；`tests/test_update_bat.py` 守着这些规则。改桌面界面的 PR，验收步骤写「退出 Peter → 双击 update.bat」，只 git pull 不会更新 VEIL.exe。
- 桌面版的服务入口是 `web_agent.py`：新工具要在它和 `veil_agent.py`（终端）两处都接上（099 就漏过一次）。工单 101 的附件工具只在 desktop `serve_chat` 能提供安全 Lab 读取与 Hub 保存回调时显示。
