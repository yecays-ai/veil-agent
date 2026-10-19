# 交接

新窗口先读 AGENTS.md → STATUS.md → 本文件。

## 上一位停在哪

- 005 / 005b 已合并（main 5971317）。lan 验收：自动保存、/memory、/forget、/restore 通过；随后聊久了出现“上下文太长”并退出。Claude 在 `ticket/005c-long-context` 修复：超出时只丢这次请求里最旧的整轮原文并提示一次；输入本身太长才拒绝，不退出。
- 006（工具调用）lan 说先不做，在和另一个 chat 讨论方向。调研文档是 Claude Doc「下一阶段可复用组件调查」。

## 下一个窗口第一步

等 005c 合并和 lan 的验收反馈。不开 006，除非 lan 说开。

## 接入结构和注意

- memory_agent.py：官方 MCP stdio 桥（单个异步任务负责子进程生命周期）+ 后台补记/召回/隐藏状态。主程序接入 system、四轮完成通知、管理命令和共用预算；Store / Budget 使用可重入锁。
- setup_memory.py 下载未改动固定 Ombre 115d831d979bf67efc7150f976e2cb34c333c935，校验 ZIP 和原始 requirements 字节哈希，独立 venv。3.12 用上游 lock，3.11 用上游声明范围。整个 .deps 已忽略。
- memory/_agent/state.jsonl 只追加调度/尝试/已存编号/隐藏状态；原文仍在原 chat.jsonl，原生记忆仍为 Markdown。第一次启用不批量回填旧聊天。备份须退出后复制整个 memory 目录。
- FastMCP 会预解析 JSON 形状的字符串；source_content 必须用 user:/assistant: 前缀原文，不能把 JSON 数组字符串直接传入，否则上游把它转成 list 并拒绝。已用真适配器复现并验证修复。
- 普通检索只取结构化命中 ID，忽略随机联想；feel 输出按 bucket_id。情感来源桶仅内部使用，不注入模型。原生 trace(dont_surface=1/0) 加本地过滤，覆盖上游 feel 未执行隐藏策略的情况及开场缓存。
- 失败组下个四轮时与新组共用一次 JSON 请求，组 ID 隔离，尝试在请求前落盘，最多两次。已确认条目的原话签名阻止正常重试/重启重复保存；硬断电落在原生写成功与本地确认之间仍有无法完全消除的确认窗口。
- 共享预算覆盖聊天、补记和向量。向量金额按保守输入上限估算，包含内部检索、索引、查重和 SDK 的最多两次内部重试；本程序不再重发。上游后台索引队列关闭。此估算不是供应商实际用量，当前免费模型金额为零；换收费模型必须同步改单价。
- 通知只在输入/回复边界展示，不在输入中插字；退出会完成排队补记再关子进程。实际聊天模型抽取质量和角色口吻尚需 lan 体验。
- 本机 .env、persona、data 不提交。characters/peter/character.toml 的 thinking="enabled" 是 lan 的本地试验，只提交新增 memory 配置，不提交该差异。
- 不动 yecays-ai/veil，不使用旧仓库。Codex 实现开 PR，Claude 负责评审合并，不启用自动合并。
