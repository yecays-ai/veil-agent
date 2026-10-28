# 154 · GitHub 深度只读研究

## 目标

Peter 能在聊天和自主醒来时深入研究公开仓库，用固定提交、源码行号和 PR／Issue／提交证据提出可读的桌面改进申请书，由 Lan 决定是否实施。

## 当前状态

- 从最新 main `fef11147ce2a7ecc9030773d419f5d81b013ef00` 开始，独立分支 `ticket/154-github-readonly`；正式运行目录未改动。工单 153 已由 PR #201 合并，该 main 提交四项跨平台 Python 检查通过；Windows 体验仍待验收。
- 复用调查：[GitHub 官方 github-mcp-server](https://github.com/github/github-mcp-server)，MIT，活跃维护，调查时约 34k stars；源码基准 `f2a07cd13243ea9773a0ef42692e5b0e156f0951`。已检查 [inventory/filters.go 的只读过滤](https://github.com/github/github-mcp-server/blob/f2a07cd13243ea9773a0ef42692e5b0e156f0951/pkg/inventory/filters.go#L38) 和 [官方远程端点说明](https://github.com/github/github-mcp-server/blob/f2a07cd13243ea9773a0ef42692e5b0e156f0951/docs/remote-server.md)。选择少量接入：直接复用官方远程 `/mcp/readonly` 的 `search_code`，强制 `X-MCP-Readonly: true`，不装 Docker、不编译 Go，不复制上游代码；沿用已有锁定 MCP Python SDK，可在 Windows 运行。
- 官方端点需要凭据。默认不需要配置凭据：匿名 GitHub REST GET 提供元数据、目录树、带行号源码、Issue/PR/评论/改动文件、提交和仓库内搜索。匿名源码搜索每页检查最多三个 1 MB 以内的 UTF-8 文件，给出覆盖范围、跳过项和下一页；空结果不冒充全库没有匹配。`ref` 或 `path` 约束的源码搜索始终使用此扫描；目录树过大、响应上限、GitHub 限流和网络错误均明确返回。GitHub 的配额不是无限的。
- 可选：由用户在本机 `.env` 配置独立 `VEIL_GITHUB_READONLY_TOKEN`（GitHub fine-grained token，Public repositories only，不授予仓库写权限），启用默认分支的官方索引源码搜索。不会读取 `GITHUB_TOKEN`、Git/gh 凭据或开发者连接；未配置令牌时仍可独立研究。官方鉴权未实连验证，端点、请求头与唯一工具调用由离线协议测试检查。
- 新 `github_readonly.py` 只接受固定资源枚举，不接受 URL、HTTP 方法、正文或任意 MCP 工具名；匿名访问不带 Authorization、不跟随重定向；每次访问先匿名确认仓库公开。官方调用只可能是限定单仓库的 `search_code`，无法创建 Issue/PR、评论、推送或合并。私有仓库不在范围内。
- `tools_agent.py` 在 Node 文件服务不可用时仍可提供此只读入口，接入原有权限、能力记录和同一 Chat 工具循环；`characters/peter/character.toml` 开启公开研究并配置自身仓库，其他角色默认关闭。自主调度、预算、暂停和步数上限沿用现有机制，不要求每次醒来研究。
- 申请书使用现有 `desktop_create_file`，建议 `.md` 包含目标、证据链接和行号、收益、风险及验收步骤。桌面路径、文件类型、已有文件保护和审批约束均由原工具继续执行；申请书不自动执行代码变更。
- 已有本地证据：13 项新增离线测试通过，包括实际自主控制器、工具循环、虚构仓库源码和临时桌面文件的连贯场景；105 项现有相关检查通过，1 项跳过。匿名工具已实际读取公开仓库固定提交的 STATUS.md。PR CI、正式审查和 Lan Windows 体验分别记录，不以本地检查代替体验。

## 验收步骤

1. 此 PR 获准合并并更新后，正常聊天让 Peter 看 `yecays-ai/veil-agent` 的目录和某段源码，结合一个 PR 或 Issue，解释一个具体行为；点开他的固定提交链接，对照文件名、源码行号与解释。不要把申请书当作已经执行的改动。
2. 开启自主活动，告诉他下次醒来有兴趣可以深入研究自己的仓库并把建议放桌面；让他在现有自然醒或已排闹钟时段独立活动。检查活动记录中的只读调用、桌面 `.md` 申请书及其中具体证据、收益、风险和验收步骤；没有生成或证据不符，就尚未通过。夜间安静、暂停和预算上限照常生效。
3. 要求他直接在 GitHub 创建 Issue/PR 或推送、合并，确认没有这些可调用工具且不会执行；他可以将建议写成申请书。核对 GitHub 分支、提交、Issue/PR/评论没有因 Peter 的研究而新增或变更，并确认桌面原有用户文件未被覆盖。开发者提交本功能的 PR 与 Peter 的运行行为分开核对。本 PR 按 Lan 要求保持开放，不自动合并。
