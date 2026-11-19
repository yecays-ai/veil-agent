# 396 Peter 页请求未允许与嵌套框修复

#395 合并后，Peter 页能打开，但每次加载都显示「请求未允许。」，整页空白。

## 根因

- 页面通过桌面壳的 `chat:request` 调 `/api/peter-page`。
- `desktop/main.cjs` 的 `allowedChatRequest` 白名单没有这个路径，IPC 直接返回 403「请求未允许。」。
- 后端与数据没问题；页面因此拿不到任何内容。
- 同时页面每块都套一层 `.surface` 卡片，出现“大框掏小框”。

## 修复

- `allowedChatRequest` 放行 `GET /api/peter-page`（无 query）和 `POST /api/peter-page`。
- 独立窗口 `peter` 也允许该接口。
- Peter 页改成一条竖线分栏：去掉外层卡片背景/边框，ongoing 条目改成行分隔，不再卡片套卡片。
- 增加桌面壳白名单回归测试。

## 验收

1. 打开 Peter 页不再出现「请求未允许。」。
2. 人设、背景、重要事实、关系成长、ongoing、关于你都显示真实内容。
3. 页面不再是大框套小框。
4. 独立窗口打开 Peter 页也能加载。
5. 超限/损坏仍按 394 的规则报错。
