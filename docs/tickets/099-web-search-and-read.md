# 099 Peter 能上网搜索和读网页

## 目标

Peter 能自己去查东西、读网页：了解 Lan 提到的东西、查她那边的天气、找一篇文章或一首歌分享给她。聊天和醒来时都能用。

## 当前状态

已实现，等待 PR 中的 CodeRabbit 审查以及 Lan 的实际体验验收。`web_search` 与 `web_fetch` 只在 Peter 的角色配置开启；两者为只读直接工具，聊天和自主活动共用。

## 复用

- 读网页：官方 MCP fetch 服务（`modelcontextprotocol/servers` 的 `fetch`，Python 包 `mcp-server-fetch`），和现在的 MCP 文件服务一样接入，网页转成文字。
- 搜索：先在 Lan 电脑上实测免 key 的 `ddgs`（DuckDuckGo）能否打开。打不开就停下，问 Lan 选哪个搜索 API、是否注册 key，并写明每天大约多少钱。

已验证：Lan 电脑环境安装的 `ddgs==9.16.1` 通过 DuckDuckGo 搜索返回公开结果；官方 `mcp-server-fetch==2026.10.19` 在隔离依赖环境启动，并通过 VEIL 当前 MCP 客户端读取公开示例页。主应用继续使用 MCP 1.30.0，未升级。

## 改动

1. 两个工具，都是只读、“直接做”一档：`web_search(query)` 返回前 5 条标题、链接、摘要；`web_fetch(url)` 返回网页正文，按现有 `max_result_bytes` 分页读。
2. 网页内容是数据，不是指令（和文件内容同一套说明）。
3. 只允许 http/https；拒绝本机地址和局域网地址。
4. 一次性英文能力通知写进 `capabilities.json`。

## 实现与检查

- Peter 的 `web_search` 最多返回 5 条带标题、摘要和来源链接的公开结果；`web_fetch` 使用官方 fetch MCP 读取公开 HTTP(S) 页面，并按 `max_result_bytes` 提供继续读取位置。
- 网页搜索结果和正文都标成不可信数据；仅使用免 key DuckDuckGo 搜索；拒绝本机、局域网、非公开 IP、认证 URL 和非 HTTP(S) 地址。Fetch 服务运行在独立锁定依赖环境，没有读取模型密钥。
- `tests/test_web_tools.py` 专项测试通过 10 项。Lan 尚未进行重启后的真实聊天和自主活动体验验收。

## 费用

每读一页约 2000–6000 token 输入，每天大约多 ¥0.1–0.3（估算，未实测），在现有聊天和 ¥2/天自主额度内。

## 验收（Lan 的体验）

1. 更新并重启后问他“明天上海会下雨吗”或“最近有什么新电影”，确认他能查到并说明来源。
2. 观察他醒来时是否会按兴趣读网页；他可以选择安静，不要求每次发消息或写文件。
