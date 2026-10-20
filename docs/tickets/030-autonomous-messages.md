# 工单 030：Peter 自主活动主动消息（4C-2）

## 目标

让 Peter 在自主唤醒时自行选择是否通过现有聊天发送最多三条消息，并让消息进入当前角色的聊天历史。

## 当前状态

基于 main `74f59b8b93d8b5cd65537e6e6a070208118891fb`。实现沿用现有 `send_message`：消息写入角色 `chat.jsonl`，`Hub.output` 将普通 assistant 消息推到网页事件流；唤醒上下文明确这是可选能力。最终自主总结仍只写自主活动记录。

复用调查：Fount 的 [wakeScheduler](https://github.com/steve02081504/fount/blob/6d70452ddbc335efbda1867a2e79f9191f95cb64/src/public/parts/shells/chat/src/reply/wakeScheduler.mjs) 用运行标记和序号处理并发唤醒，[async-task registry](https://github.com/steve02081504/fount/blob/82af83ba298146b1da844584f53012cb768cb064/src/public/parts/plugins/async-task/registry.mjs) 用任务 ID 与 generation ID 标记执行归属。只借鉴并发保护与稳定身份思路，沿用 VEIL 现有聊天存储和自主调度，不复制代码或增加依赖。仓库约 693 stars；LICENSE 文件存在但接口未能解码校验，因此不复制其代码。

实现已加入逐唤醒/工具调用标识、持久三条上限、网页事件输出及让位同步保护，并沿用 `capabilities.json` 一次性通知。定向测试 `test_autonomy.py`、`test_tools.py`、`test_chat.py` 合计运行 94 项，92 项通过、2 项可选集成跳过，并通过 `py_compile`。HTTP 网页套件因当前沙箱禁止本机回环 socket 未能验证；Windows 真实体验待 Lan 验收。

## 验收步骤

1. 在安全的自主唤醒中，让 Peter 自行选择发送一条留言；确认网页出现普通 assistant 气泡，并可在同一活动中先做 lab 工作再留言；另一次让他只做事或不行动，确认没有额外气泡。
2. 刷新网页并退出、重启服务，确认留言保留且只出现一份；主动打开前正常聊天，确认后台让位后不再发送。
3. 让测试唤醒尝试四条消息，确认只保存三条且第四条明确拒绝；回归检查 `/cost`、记忆和文件审批。
