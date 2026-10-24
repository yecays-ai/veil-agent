# 102 Peter 和 Lan 在 QQ 上聊天

## 目标

Lan 不在电脑前时，用手机 QQ 找 Peter 的 QQ 小号，他用这个小号回她；平时他也可以自己决定在 QQ 上找她。上下文和记忆与桌面是同一套。

## 当前状态

- Peter 只能在桌面聊天里说话；邮件（098）要等他的 QQ 邮箱满 14 天（2026-11-07）才能开服务。
- Lan 的电脑一直开着，Peter 的 QQ 小号可以登录在这台电脑上。

## 复用

不注入、不改协议：照 [QQSafeChat](https://github.com/TheD0ubleC/QQSafeChat)（MIT）和 [qq-operations](https://github.com/treers2/qq-operations)（MIT）的做法，用 Windows UI Automation（`uiautomation` 包，Apache-2.0）读 QQ NT 聊天窗口的消息气泡、用剪贴板粘贴并点“发送”。QQ NT 的控件类名取自 qq-operations。

## 改动

1. 配置：本机 `.env` 放 `QQ_LAN_CONTACT`（Peter 的 QQ 里和 Lan 聊天时窗口标题/顶部显示的名字）。角色配置 `[qq] enabled`、`poll_seconds = 20`、`idle_seconds = 120`、`daily_max_sent = 100`。没配就没有这个功能。只在 Windows 上启用。
2. 读：只有键盘鼠标超过 `idle_seconds` 没人动、而且前台不是全屏程序（看视频）时，才去读和 Lan 的那个单独 QQ 聊天窗口；需要时把它短暂拉到前面，读完把原来的窗口放回去。程序自己的点击和打字不算“有人在用电脑”。启动后第一次读到的内容算旧的。
3. Lan 的新消息（对方气泡）合成一条，前面加事实说明 `[Lan sent this on QQ; she is away from the computer.]`，走正常聊天流程（同一份聊天记录、同一份记忆、聊天预算）。Peter 正忙就下次再收。
4. 回复：这一轮 Peter 说的话按气泡分条在 QQ 发回。平时他用 `send_message` 的 `qq: true` 主动发。都要等 Lan 不在电脑前才发。
5. 防发错人：打字前核对聊天窗口顶部名字含 `QQ_LAN_CONTACT`，粘贴后核对输入框内容，不一致就清空、不发。剪贴板用完恢复原文字。
6. 每天最多 `daily_max_sent` 条，记录在 `data/<角色>/qq-log.jsonl`（只有时间和收/发）。
7. 失败（窗口找不到、锁屏、界面变了）在桌面显示一行中文原因，同样的原因不重复显示。
8. 一次性英文能力通知写进 `capabilities.json`。

## 验收（Lan 的体验）

1. 电脑设置成不自动锁屏（可以关显示器）。在电脑上登录 Peter 的 QQ 小号，加你为好友，双击你打开一个单独的聊天窗口并一直开着。`.env` 填 `QQ_LAN_CONTACT=` 那个窗口顶部显示的名字，更新并重启。
2. 离开电脑两分钟以上，用手机 QQ 给他发消息，一分钟左右收到他在 QQ 上的回复。
3. 回到电脑前，桌面聊天里能看到刚才 QQ 上的对话，接着聊他记得。
4. 你在电脑前用鼠标键盘、或全屏看视频时，QQ 窗口不会突然跳出来。
