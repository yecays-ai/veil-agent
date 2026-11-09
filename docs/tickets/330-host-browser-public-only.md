# 工单 330：宿主通道的后台浏览器只能上公网

## 问题

工单 329 的检查点只检查 Peter 主动打开的网址。网页里的链接、重定向、脚本和 iframe 可以把后台浏览器带进家里的内网（比如路由器页面）或本机服务。

## 做法

- 后台浏览器（Playwright MCP 0.0.84）启动时加 `--proxy-server http://127.0.0.1:<端口>` 和 `--proxy-bypass <-loopback>`。这个代理复用 `browser_tools.PublicProxy`：只接受到公网 IP 443 端口的 CONNECT，由代理自己解析 DNS 并固定 IP（防 DNS 重绑定）。单次运行最多 100 MB。
- 代理在 `host_executor.prepare` 里启动（`BrowserNetwork`），跟着浏览器网关一起关闭。代理起不来，这次运行就不提供浏览器。
- 明文 HTTP 网站打不开（只走 HTTPS）。

## 验收

- 单元测试：代理拒绝到 127.0.0.1、192.168.x、10.x、localhost 和 80 端口的连接，也拒绝明文请求；网关退出时会关闭代理，即使网关从没启动过。
- 云端：Chromium 经这个代理访问 127.0.0.1 上的网页失败。
- 未验证：Lan 电脑上的 Edge（并入工单 329 的真机验收）。
