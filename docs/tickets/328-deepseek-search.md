# 工单 328：Peter 的搜索改用 DeepSeek 官网的搜索接口

## 目标

Peter 搜索时不再靠几个境外免费引擎碰运气：只要本机保存了 DeepSeek 官网的 key，就先用 DeepSeek 官网自己的搜索接口；它出不了结果，再退回原来的免费引擎。

## 当前状态

Peter 的 `web_search`（内部叫 `rabbit_hole`，`web_research` 也用它）靠 `ddgs` 库抓 brave、yahoo 等 5 个境外免费引擎。Lan 的电脑开着加速器（出口在美国机房），各家把它当成机器人：真实日志里三次搜索失败两次，成功的那次才拿到 7 条。DeepSeek 官网的模型自己不能联网（远程控制实测，三种问法都回答“无法联网”），但 DeepSeek 官网 2026-10 起提供单独的搜索接口：`POST https://api.deepseek.com/v1/tools/search`，用的就是 DeepSeek 的 API key，价格公布为每千次成功请求 5 元，失败、超时和空结果不收费（文档 `api-docs.deepseek.com/guides/search`）。

## 改动

- `web_search`/`web_research` 的搜索顺序里加入 `deepseek`：本机 `.env` 里有 `DEEPSEEK_API_KEY` 时排在最前面，没有就跳过；结果用接口给模型看的 `summary`（没有就用 `snippet`）。
- 每次成功搜索按 ¥0.005 记入费用账本（`costs.jsonl`，名字 `deepseek-search`）；失败、超时、空结果记 0。共享预算超限时跳过 DeepSeek，直接用原来的免费引擎，不报错。
- 请求只带搜索词（最多 128 个字符）和结果数 8，不带页面全文；网址仍经过公网地址过滤；报错里不会出现 key。
- 总时限仍是 20 秒，每个引擎失败的原因照旧写进报错和日志。

## 接入检查

- 入口：主聊天、自主醒来、`web_research`、委派任务里的 `web_search` 都走同一个 `WebTools._find_results`；工作台容器里的 OpenCode 不走它。
- 停止与恢复：不变，仍受 20 秒总时限和停止键约束。
- 权限：key 只从本机 `.env` 读取，不读环境变量，不进日志、结果和仓库；网址过滤不变。接口是只读的。
- 费用：约每次 ¥0.005（官网公布价），记入共享账本并受每天、每月上限约束；按 Lan 现在每天几十次搜索算，每天不到 ¥0.5。
- 数据：不新增文件；`costs.jsonl` 里多出 `deepseek-search` 行。
- 状态可见：`/cost` 的“今天按模型”里能看到 `deepseek-search`；搜索失败的原因照旧写在 `tools.jsonl`。
- 升级回滚：不需要迁移配置；没有 DeepSeek key 时行为和以前完全一样；还原本 PR 即可回退。
- 真实环境：开发者用远程控制在 Lan 电脑上让 Peter 搜几次，核对结果和 `costs.jsonl`。

## 验收步骤

1. 更新并重启 Peter，对他说“帮我查一下今天有什么科技新闻，读两个网页告诉我”：能搜到并读到网页。
2. 输入 `/cost`（或看今天按模型的花费）：多了一行 `deepseek-search`，一次搜索约 ¥0.005。
