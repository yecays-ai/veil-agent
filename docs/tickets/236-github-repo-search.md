# 工单 236：Peter 能按关键词找 GitHub 仓库并比较

## 目标

Peter 不知道仓库名也能在 GitHub 上找项目：关键词搜索、翻页、排序，拿到候选后继续读它的 README、许可证、目录和源码，做出带来源的比较。聊天、自主活动、委派只读搜索都能用，仍然只读、只看公开仓库、受现有预算和权限限制。

## 当前状态

`github_read` 新增两个资源，沿用原来的工具和 GitHub 官方 REST 接口，没有新的工具或依赖：

- `search_repositories`：不需要 `repository`；参数 `query`（普通关键词）、`sort`（`best_match`/`stars`/`updated`/`forks`）、`page`。每页 10 个，返回仓库名、链接、描述、Stars、许可证、语言、最近更新和推送时间、是否归档、forks、open issues。只列公开仓库。
- `readme`：读指定仓库的 README（不用猜文件名），带行号和链接。

许可证和活跃度在原有的 `resource=repository` 里；目录、源码、issues、提交历史仍是原来的资源。其余资源仍必须给 `owner/repo`。匿名请求的 GitHub 速率限制照旧（搜索每分钟约 10 次，其余每小时约 60 次）；超限时返回「稍后再试」。

## 验收步骤（Lan 在 Windows 上做）

1. 更新并重启 Peter，不给任何仓库名，说：「帮我找五个适合 VEIL 的成熟开源 Agent 项目，比较它们的功能、许可证和维护情况。」Peter 应自己搜索，再读候选的 README、许可证和最近更新，给出带链接的比较。
2. 说「再按最近更新排序找一遍」：换成 `updated` 排序，或翻到第 2 页。
3. 自主活动里让他研究同类问题（或观察活动记录）：同样能搜，且不能创建 PR、不能写 GitHub。
