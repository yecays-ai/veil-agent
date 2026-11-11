# 工单 357：iPhone 与现有 Peter 双端文字聊天

所属：C1（多端接入）。
做完后 Lan 能在装有 Tailscale 的 iPhone Safari 打开一个独立网页，与 Windows 桌面端正在运行的同一个 Peter 收发文字消息，并在两端看到同一段对话。
Lan 验收：电脑与手机同时在线交替发消息，断网重连后不重复发送、不丢已确认消息，关闭手机接入后原地址不可用。

## 目标

用 Tailscale Serve 安全发布一个默认关闭的最小移动聊天页，只接现有聊天事件与文字发送，不建立第二个 Peter、第二份记录或公网入口。

## 当前状态

审计见 `docs/tickets/356-iphone-access-audit.md`。现有 `Hub.snapshot`、`Hub.submit`、持久聊天、请求 ID 去重和轮询可直接复用；现有桌面页依赖 Electron preload，且本机 API 的 Host/Origin/Token 边界不能直接放给 Safari。

成熟方案选择 Tailscale Serve（官方 Windows/iOS 客户端、tailnet HTTPS 和 ACL）；Cloudflare Tunnel + Access 只作以后无需 iPhone VPN 客户端时的备选。本单不复制第三方代码、不增加前端框架。

改动仅限：`web_agent.py` 的移动静态页与两个专用 API、`desktop/mobile.html|js|css`、现有设置/启动中的默认关闭开关与状态、对应 Web/UI 测试和状态文档。不得改聊天、记忆、自主活动和模型逻辑，不得放行其他 `/api/*`。

## 接入检查

- 入口：设置中显式开启；展示 tailnet HTTPS 地址与连接状态；关闭时撤销 Serve。
- 停止与恢复：VEIL 未运行显示离线；Safari 回前台从最后事件编号续取；Serve 使用后台模式恢复。
- 权限：仅配置的 Tailscale 身份；只允许移动事件读取和文字发送；不下发内部 Token。
- 费用：复用同一 Peter 与现有预算；重复请求 ID 不产生第二次模型调用。
- 数据：复用同一聊天持久记录；移动端只在 `sessionStorage` 留事件游标、草稿和待确认请求 ID。
- 状态可见：手机显示连接中、离线、Peter 忙碌、发送未确认；桌面现有状态不变。
- 升级回滚：功能默认关闭；关闭即停止移动入口，不改变历史数据或桌面聊天。
- 真实环境：必须在 Lan 的 Windows + iPhone Safari + Wi-Fi/蜂窝网络完成以下验收。

## 真机验收

1. Windows 开启 iPhone 聊天，iPhone 登录同一 tailnet，用 Safari 打开显示的 HTTPS 地址；手机能看到最近对话，未登录/非允许身份或关闭功能后均无法访问。
2. 桌面发“电脑一”，手机看到；手机发“手机一”，桌面看到且 Peter 只回答一次；Peter 忙时另一端发送被明确保留而不是静默丢失。
3. 手机发送后立即切飞行模式，再恢复 Wi-Fi；页面核对同一 `request_id`，消息最多处理一次。随后切到蜂窝、锁屏再回前台，已确认消息仍在且继续收到新消息。
