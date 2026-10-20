# 工单 028：最小长期任务续做（跨自主唤醒）

## 目标

Lan 可交给 Peter 一项需要多次自主活动完成的工作；Peter 可自行建立长期任务，保存阶段成果、证据和下一步，并在后续唤醒或服务重启后安全续做。每个角色同时最多一项未完成任务。

沿用当前自主活动每次 12 个工具步骤、最长 10 分钟、后台 ¥2/日与总预算限制、聊天优先、暂停和服务退出行为；任务状态不授予文件或记忆权限。

## 当前状态

- PR #58 已合并，基线 main `a7ab987`。本分支新增角色隔离的原子 JSON 状态 `data/<role>/long-task.json`、状态读取/更新工具、自主上下文续接、`/autonomy` 摘要和一次性能力通知。
- 自主活动中的副作用工具执行前先保存待核实检查点，返回确定结果后保存短结果记录；进程在两者之间退出时，下次先检查并要求结果证据，未核实不能盲目重做。完成状态要求提交最终结果证据。
- 复用调查（2026-10-21）：VEIL 直接复用 `autonomy.py` 的 `AutonomyController`、`data/<role>/autonomy.jsonl`、角色配置预算、`Chat` 工具循环、`Store` 与 `os.replace` 原子写法，以及现有权限和审批层。fount 当前 `master` 的 [`wakeScheduler.mjs`](https://github.com/steve02081504/fount/blob/74c012c2d7df69f11402866ddcf00fb1f1fa72cf/src/public/parts/shells/chat/src/reply/wakeScheduler.mjs)、[`timers.mjs`](https://github.com/steve02081504/fount/blob/ba24d50dcf0c04947130a7689989f8f5526d78c7/src/server/timers.mjs)、[`async-task/registry.mjs`](https://github.com/steve02081504/fount/blob/2f9fd89f4b9ac061dab3b751fc43add2f812b4b0/src/public/parts/plugins/async-task/registry.mjs) 用于参考唤醒去重与生命周期；异步注册表是内存态，不能充当持久状态库。检查时仓库页显示 691 stars；fount 许可未核实，因此不复制源码。LangGraph 当前持久化文档将线程检查点用于恢复执行，MIT；仅参考“保存状态并从检查点恢复”，本单不引入其框架或依赖。Temporal Python SDK（MIT）和 Agno（Apache-2.0）只作范围对照；持久工作流运行时与通用 Agent 框架超出本地单任务需求，均不接入。未复制外部源码或增加依赖。
- 假数据定向验证：`test_long_tasks.py` 8 项、`test_autonomy.py` 11 项、`test_tools.py` 44 项通过（2 项可选集成跳过）；相关文件编译通过。Windows 网页体验尚未由 Lan 验收。

## Windows 验收步骤

1. 在网页让 Peter 建立三部分 lab 学习任务，检查 `/autonomy` 显示名称、状态、已完成工作和下一步；等第一次自主活动只完成第一部分，确认整项仍是 active。
2. 正常退出并重启服务；确认任务和第一部分仍在。让后续唤醒完成第二、第三部分，核对文件没有重复写入，任务显示 completed，之后唤醒不再继续提示该任务。
3. 任务进行中发聊天消息并暂停/恢复自主活动，确认聊天正常、进度保留；检查 `/cost`、文件权限、记忆审批和 Peter/Ghost 任务隔离。
