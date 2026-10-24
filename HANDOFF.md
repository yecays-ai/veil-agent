# 交接

## 当前进度

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

102 已合并（#144）。103：`QQWindow._auto` 缺 uiautomation 时抛 `QQUnavailable`（提示重跑 setup.bat），`AwayBridge` 两个线程捕获意外异常并报一次。Lan 的电脑第一次没重跑 setup.bat 就踩到了这个。105：发送前核对用 `comparable()`（去掉表情、空白），因为 QQ 把表情转成图片读不回文字。104：QQ 界面元素没有类名，`parse_chat` 按名字解析（常量在 `qq_channel.py` 顶部）。若 QQ 升级后读不到，先在 Lan 电脑上跑 `.local-tmp\qq_check2.py` 打印当前结构（本地诊断脚本，不在仓库里）。101 做完后，邮件和 QQ 回复要带上附件。 107：105 的猜测不对，真正原因是 uiautomation 2.0.29 `SetClipboardText` 按码点数分配 UTF-16 缓冲，超出 BMP 的表情占两个单元，句尾被截断；现在用 `qq_channel.set_clipboard_text`（按 UTF-16 字节长度）。不要换回 `auto.SetClipboardText`。

## 边界

- 不注入 QQ、不用协议；只在 Lan 两分钟没碰电脑且前台不是全屏程序时操作窗口；打字前核对收件人。
- 授权码和 Lan 的 QQ 名字只在本机 `.env`。
