# 交接

## 当前进度

- 工单 106 在 `ticket/106-activity-window`，PR #149，基线 `566280d`。Lan 已确认效果图；按 Lan 要求，主动请求 CodeRabbit 审核后停止，由 Lan 自行跟进评论与后续合并。
- Claude 审查补丁：桌面每个窗口每 600 ms 轮询 `/api/events`，原实现每次都在 Hub 锁内重读并解析 tools.jsonl 最近 5000 行（大账本约 1 秒），会卡住聊天；现在只在账本大小/修改时间变化时重算，并在锁外计算。
- 已完成真实工具开始/结束同 `call_id` 账本、`/api/events` 折叠投影、统一活动时间线与筛选；窗口管理已扩成聊天/活动/记忆/更多四个标签均可同时拖出、独立移动缩放、分别合并与恢复各自模式/位置/尺寸，仍共用同一 `chat.html`。未改能力通知或 Peter 上下文。
- 自动检查：活动账本 3、Ombre 50、聊天 65、网页/API 54、Autonomy 26、长期任务 20、Desktop 全套 6 均通过；真实 Electron renderer 已分别渲染聊天/活动/记忆/更多独立窗口并截图。PR #149 已创建；下一步由 Lan 跟进 CodeRabbit 评论，真实 Windows 手势拖出、合并与重启恢复尚待合并后体验。

- 当前工作：工单 099，PR #148，分支 `ticket/099-web-search-and-read`，基于最新 `main` `85b2491`。联网搜索和网页只读已实现；专项测试 10 项通过。接下来在 PR 对话发起 CodeRabbit 审查，收到评论即停止本工单，不合并。
- 099 的 `ddgs==9.16.1` DuckDuckGo 搜索已在当前 Windows 环境验证；官方 `mcp-server-fetch==2026.10.19` 也在隔离环境成功启动并读取公开页面。Lan 尚未体验验收。详情和步骤见 `docs/tickets/099-web-search-and-read.md` 与 `STATUS.md`。

- 097 已合并（#143），098 邮件已合并（#142，邮箱 11-07 后才能验收）。
- 102 QQ 由 Claude 在分支 `ticket/102-qq-with-lan` 实现：`away_bridge.py`（通用 `AwayBridge`：渠道消息进同一段聊天、本轮回复走原渠道、`send_message(<渠道>=true)` 主动发），`email_channel.py` 改为实现这个接口，`qq_channel.py`（`QQChannel` + `QQWindow`，uiautomation，Windows 才启用）。
- 新依赖 `uiautomation==2.0.29`（及 `comtypes==1.4.17`），只在 Windows 安装。
- 测试：`tests/test_email_channel.py`、`tests/test_qq_channel.py` 通过（QQ 窗口用假对象）。真实 QQ 窗口操作只能在 Lan 的 Windows 上验收。

- Claude 审查 #148 时补了一处：桌面版实际走 `web_agent.py` 启动服务，原先只在 `veil_agent.py`（终端）接入了 `WebTools`，桌面聊天里 Peter 拿不到网页工具；现在两处都接入。

## 099 本窗口停止点

在 PR #148 发起 CodeRabbit 审查并等待其评论；评论到达后停止，不触发 Cubic、不合并 PR，也不开始其他工单。之后交 Lan 按 `STATUS.md` 的 099 步骤进行体验验收。

## 其他已完成工单交接

102 已合并（#144）。103：`QQWindow._auto` 缺 uiautomation 时抛 `QQUnavailable`（提示重跑 setup.bat），`AwayBridge` 两个线程捕获意外异常并报一次。Lan 的电脑第一次没重跑 setup.bat 就踩到了这个。105：发送前核对用 `comparable()`（去掉表情、空白），因为 QQ 把表情转成图片读不回文字。104：QQ 界面元素没有类名，`parse_chat` 按名字解析（常量在 `qq_channel.py` 顶部）。若 QQ 升级后读不到，先在 Lan 电脑上跑 `.local-tmp\qq_check2.py` 打印当前结构（本地诊断脚本，不在仓库里）。101 做完后，邮件和 QQ 回复要带上附件。

## 边界

- 不注入 QQ、不用协议；只在 Lan 两分钟没碰电脑且前台不是全屏程序时操作窗口；打字前核对收件人。
- 授权码和 Lan 的 QQ 名字只在本机 `.env`。
