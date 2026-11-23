# 工单 425 · 经历页的两个 GET 路由没有接上

## 背景

第五步正式迁移后做 Windows 冒烟时发现：`/api/memory/events`（经历列表）和 `/api/memory/event`
（单条经历详情）返回 404。`_get_memory()` 里两段处理代码从工单 407 起就存在，但 `do_GET` 只把
`/api/memory/catalog`、`/api/memory/read` 转给它，所以桌面端「记忆 · 经历」页的列表和详情一直打不开。

事件层默认关闭（#421 起 main 才打开），这个页面此前没人用，因此没暴露；69 篇迁移后它就是查看
事件的唯一入口。

## 目标

- `GET /api/memory/events` 返回事件列表（`items`、`count`）。
- `GET /api/memory/event?id=...` 返回单条事件详情；不存在时 404。
- 两个路由都校验 `X-Token`（错误 token 403），和目录/读取接口一致。

## 改法

`web_agent.py` 的 `do_GET` 分支把这两个路径一起转给 `_get_memory`，加注释说明 425。
不动接口返回结构、不动页面、不动事件数据。

## 验收（lan 在 Windows 上做）

1. 合并后 `update-main.cmd`，重启 VEIL。
2. 打开「记忆 · 经历」：应能看到 69 篇事件列表，点进去能读完整故事和阶段。
3. 拆/移事件按钮仍可用（它们走的是本来就已接上的 POST 路由）。
