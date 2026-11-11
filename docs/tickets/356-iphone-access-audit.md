# 工单 356：iPhone 接入技术审计

所属：C1（多端接入）。
做完后 Lan 能明确选择 iPhone 接入 Peter 的安全路线，并直接开下一张最小开发工单。
Lan 验收：读本报告，确认连接方式、代码改动、安全边界和真机验收步骤都足够明确。

## 结论

推荐 **Tailscale Serve**：Windows 与 iPhone 加入同一个 tailnet，Windows 用 `tailscale serve --bg <VEIL 移动端口>` 把仅监听 `127.0.0.1` 的移动网页以 tailnet 内 HTTPS 地址提供给 Safari。iPhone 必须安装 Tailscale（iOS 15+）并登录；不使用 Funnel，不开放公网端口。

这条路线保留 VEIL 的本机边界，又由 Tailscale 提供设备身份、加密链路、HTTPS、MagicDNS 和访问控制。Personal 方案目前为非商业个人使用免费，最多 6 个用户、用户设备不限。Windows 10+/Server 2016+ 与 iOS 15+ 有官方客户端。Tailscale 主仓库是 BSD-3-Clause；本工单只调用已安装产品，不复制上游代码。

Cloudflare Tunnel + Access 技术上可行，也能把 `localhost` 通过出站隧道发布并以身份策略保护；但需 Cloudflare 账户、托管域名、公开主机名、Access 应用及 Windows 服务。配置疏漏时主机名会直接公开，且 Safari/Access Cookie 的故障面更大。它保留为以后“不要求 iPhone 安装 Tailscale”的备选，不进入下一张工单。Quick Tunnel 没有稳定身份边界，不采用。

官方依据：

- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) 支持把 `127.0.0.1` 服务以 tailnet 内 HTTPS 反向代理，并服从 tailnet 访问规则；[`--bg` 会在重启后恢复](https://tailscale.com/docs/reference/tailscale-cli/serve)。
- [Windows 安装要求](https://tailscale.com/docs/install/windows)、[iOS 15+ 安装要求](https://tailscale.com/docs/install/ios)与[当前 Personal 限额](https://tailscale.com/pricing)。
- [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/)为出站隧道；[Access 自托管应用](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)默认拒绝、需显式 Allow，并要求验证 Access token；[Windows 服务配置](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/windows/)另有持久化步骤。

以上免费额度和系统要求核查于 2026-11-12，实施时应再看当前官方页面。

## 现有实现与阻碍

| 路径 | 已有能力 | 审计结论 |
| --- | --- | --- |
| 服务启动 | `web_agent.start_server` 固定监听 `127.0.0.1`；启动时生成随机 Token，写入 `data/web_service.json` | 本地绑定应保留，适合放在 Serve 后面；不能直接改成 `0.0.0.0` |
| Host / Origin / Token | `make_handler._guard` 只接受 `127.0.0.1/localhost` 的 Host、同源 Origin 和 `X-Token` | 现有接口不能经 Serve 直接访问；也不能把运行时 Token 放进手机网页或 URL |
| 桌面 UI | `desktop/chat.js` 通过 Electron preload 的 `window.veilDesktop.request` 调接口 | Safari 没有 preload，不能直接复用整份 `chat.js`；应做独立的最小移动页 |
| 实时同步 | `/api/events?after=<id>` 约每 600 ms 轮询；`Hub` 给事件递增编号并保留最近 5000 条 | 不需要 WebSocket/SSE；手机可复用同一事件游标语义，断线后从最后编号续取 |
| 历史恢复 | 聊天写入角色的持久记录；启动时按 `history_rounds` 回放为事件；待提交输入另有 `incoming-submissions.jsonl` | 手机和桌面共享同一个 `Hub`、同一个 Peter 和同一份记录，不建第二套会话或记忆 |
| 并发与去重 | `Hub.submit` 在条件锁内原子登记；Peter 回复期间的新消息会排队；两个请求同时处于落盘登记阶段时一个可收到 409 busy；客户端提供 UUID `request_id`；服务端保存近期请求摘要，重复同内容幂等、同 ID 异内容冲突 | 双端同时在线可安全读取和排队发送。手机必须稳定保留请求 ID，收到明确结果前不能生成新 ID 重发；409 时保留草稿并稍后重试同一 ID |
| 中断恢复 | 前端轮询失败显示离线并重试；已接受的普通文字先持久化，再进入聊天队列；服务启动会恢复未完成输入 | 能覆盖 Wi-Fi/蜂窝切换与页面回前台，但 Safari 真机后台冻结、游标越过事件窗口仍待验证 |

## 下一单必须修改

- `web_agent.py`：增加只读静态移动页入口，以及只允许 `GET /api/mobile/events`、`POST /api/mobile/send` 的移动路由；复用 `Hub.snapshot` / `Hub.submit`，不复制聊天状态。移动路由只在显式开启后工作，只接受配置的 tailnet Host/Origin 和可信的 Tailscale Serve 身份头；现有桌面 `_guard` 与所有原接口保持不变。
- `desktop/mobile.html`、`desktop/mobile.js`、`desktop/mobile.css`：独立、无框架的窄屏聊天页；使用浏览器 `fetch`，保留事件游标与待确认 `request_id`，网络恢复后先核对事件再决定是否重试。
- 启动/设置位置（实施前以现有配置模式复核）：增加一个默认关闭的“iPhone 聊天”开关、允许的 Tailscale 登录身份和 Serve 状态提示；关闭时撤销 Serve 配置。不能自动安装、登录或替 Lan 修改 tailnet ACL。
- `tests/test_web.py`：覆盖默认关闭、远程 Host/Origin/身份拒绝、移动 API 白名单、重复请求、并发登记 409 与回复期间排队、断线游标续取，以及桌面原接口行为不变。
- `desktop/tests/ui.test.cjs`（或等价静态检查）：确认移动页无 Electron-only API、输入保留和重复提交保护存在。

不应修改 `veil_agent.py` 的聊天、记忆、模型或自主活动逻辑；手机只是同一个 `Hub` 的第二个视图。

## 风险与处理

| 风险 | 处理办法 |
| --- | --- |
| 远程用户拿到当前运行时 Token，即可调用设置、停止、审批、文件等全部本机 API | Token 永不下发手机；移动端只暴露两个专用路由，服务端白名单，不把现有 `/api/*` 整体放行 |
| 只检查 Host/Origin 可被非浏览器客户端伪造 | Serve/tailnet ACL 是第一道门；应用再校验 Serve 注入的身份并限定 Lan 的登录名。后端仍只监听 loopback；本机同用户恶意进程不在新增远程威胁模型内 |
| 手机和桌面同时发送导致乱序或重复计费 | 沿用同一个 `Hub` 队列；两端各生成 UUID；同一次重试必须复用 ID；并发登记收到 409 时保留原草稿与 ID；未确认时先拉取事件核对 |
| 已收到 200，但响应在网络中丢失 | 同一 `request_id` 重试得到 duplicate，不再次入队；移动页按 request ID 在事件中确认 |
| 网络中断或 Safari 被系统冻结 | 保存 `after` 游标和未确认请求到 `sessionStorage`；回前台先轮询再重试；超过服务端事件窗口时重新加载当前历史窗口并去重渲染 |
| Windows 重启后网页地址存在但 VEIL 尚未就绪 | 移动页显示明确离线/未就绪，不缓存成功；Serve 用 `--bg` 恢复，VEIL 仍按现有启动方式运行 |
| 手机能看到桌面管理能力或代替 Lan 点审批 | 下一单只做文字聊天；不开放审批、停止、设置、生活记录、记忆管理、附件、语音、表情和卡片动作 |
| Tailscale 账号或 iPhone 丢失 | tailnet 控制台撤销设备/用户；ACL 只允许 Lan 身份访问 Windows 的 HTTPS Serve 端口；不启用 Funnel |

## 尚未验证

- Lan 的 Windows 与 iPhone 当前系统版本、Tailscale 登录身份、tailnet HTTPS/ACL 是否已配置。
- Windows 版 `tailscale serve --bg` 在 Lan 机器上的管理员权限、重启恢复和代理身份头的实际值；必须抓取一次真实请求确认，不能只靠文档猜头名。
- iPhone Safari 在锁屏、切换 Wi-Fi/蜂窝、Tailscale 重连、长时间后台后对轮询与 `sessionStorage` 的实际行为。
- 当前桌面 UI 的完整消息类型在移动页如何降级显示；下一单只承诺文字用户/助手消息和忙碌/离线状态。
- 5000 条实时事件窗口被越过后的真机恢复体验；持久聊天不会丢，但最小移动页需验证不会重复渲染。
