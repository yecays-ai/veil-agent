# 337 Peter 自己提 PR（GitHub 令牌 + 登录检查）

## 目标
Lan 说“我看到某某功能”，Peter 自己查到仓库、读完、判断值不值得要，要的话改代码、跑测试、开 PR；Lan 合并后更新，坏了就退回。

## 现状
- 这条链路 #314/#315 已经有（`maintenance` 工具：begin → workbench 改并测试 → deliver 推送并建 PR → checks → accept → merge），推送和建 PR 各弹一次授权卡。此前真实 GitHub 写入从未验证。
- 本单只补两处缺口：
  1. 令牌：Lan 把细粒度令牌存在 `secrets\github-token.txt`（运行 `github-token.cmd`，输入隐藏），仅宿主 `git`/`gh` 通过环境变量使用（`gh` 作为 git 凭据助手）；不进聊天、记忆、仓库、日志、容器。没有令牌文件时沿用电脑上已有的登录。
  2. 开始前先检查 `gh` 是否可用并已登录，否则一句话说明缺什么，不建 worktree、不弹卡。
  3. 只改文档（`docs/` 或 `.md`）的改动，容器里本地测试红（容器缺可选依赖，基线本身就红）不再挡住 deliver；改了代码的改动仍必须测试绿。真机第一次尝试就卡在这里。
- 不新增权限、预算或停止机制。

## 验收步骤
1. 运行 `github-token.cmd`，粘贴令牌（需 `yecays-ai/veil-agent` 的 Contents 与 Pull requests 读写）。
2. 对 Peter 说：“看看某个开源项目的某个功能，值得就加到你自己里，提 PR。”他自己查仓库、读代码、决定。
3. 开始、推送各弹一次授权卡；同意后 PR 出现在 GitHub 上，分支名为 `ticket/<编号>-…`。
4. 令牌不出现在聊天、日志或 PR 里。
