# 工单 249：修复 OpenCode 工作台审批误判

## 目标

Peter 在工作台搜索包含 `git push` 等字样的普通文本时不再误弹审批，真正的发布、安装、shell 联网命令和批量删除仍须审批。

## 当前状态

`executor_opencode.py` 把 OpenCode 权限请求的 patterns 和 metadata 拼接后做关键词匹配，导致搜索文本也被当成将要执行的命令；复杂或不完整命令还需要保持保守审批。工单 248 / PR #300 已修复工作台最终结果汇总并合入，未改审批分类。Cubic 审查已发现并补强 Windows Git 可执行名、递归删除别名及 shell 替换、解释器脚本、source 等复杂命令的审批识别；审查还确认需把范围明确限定到 shell 联网命令，既有 `webfetch` 行为不在本工单改变。PR #303 的修正和最新 CI 待完成。

复用调查：OpenCode v1.19.5（MIT）现有 shell permission 请求已经提供 `patterns`（逐条命令片段）和 `metadata.command`（原始命令），见 [shell.ts](https://github.com/anomalyco/opencode/blob/237595e2421473b871f613de87e5588d8aa8acae/packages/opencode/src/tool/shell.ts)；复用这些字段即可，不复制上游代码或新增权限系统。

## 验收步骤

1. 在测试项目里搜索含 `git push` 的普通文本，确认搜索直接执行且没有发布审批卡。
2. 在临时 Git 项目执行 `git push` 并拒绝审批，确认推送未执行；复杂命令也不能因包含安全搜索而自动放行。
3. 再运行普通工作台任务，确认结果和费用正常，聊天与自主活动行为不回归。
