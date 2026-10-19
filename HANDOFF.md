# 交接

新窗口先读 AGENTS.md → STATUS.md → 本文件。

## 上一位停在哪

- main 已快进到 `54eaf84`；工单 006 已开始，当前在 `ticket/006-tools`。实现已接入工具循环、filesystem MCP 1.0.0、权限与路径闸门、逐步预算、工具侧车日志、能力清单及文档。离线套件通过；Codex 环境能找到 Node 但找不到 npx，因此真实服务器和 Windows 联接点验收未跑。
- 当前余项：PR #13 已通过本机 Git 凭据和 GitHub API 创建；Codex GitHub 连接器曾在传输层失败。旧 Windows CI 暴露角色目录路径别名误判和竞态测试触发条件问题，修复已在本地完成，待推送后复查 CI。离线全套件 85 项通过、4 项跳过。按 `.agents/skills/pr-review/SKILL.md` 等小兔子评审完成、CI 全绿再合并。不要把 `characters/peter/character.toml` 的本机 `thinking="enabled"` 试验提交；这是 lan 的本地设置。
- PR 合并后让 lan 按 `STATUS.md` 验收：目录读取、同意/拒绝新建、既有文件绝不覆盖、目录外拒绝、重启后的能力与记忆说明。DeepSeek 思考模式和真实聊天效果仍需她体验。
- 005 / 005b 已合并（main 5971317）。lan 验收：自动保存、/memory、/forget、/restore 通过；随后聊久了出现“上下文太长”并退出。Claude 在 `ticket/005c-long-context` 修复：超出时只丢这次请求里最旧的整轮原文并提示一次；输入本身太长才拒绝，不退出。
- 005c 已合并（main 83b19f8）。lan 验收反馈记忆质量差；Claude 在 `ticket/005d-memory-quality` 把 veil 仓库的维护提示词设计移植到提取层（见 docs/tickets/005d-memory-quality.md）。
- 005d、005f、005g 已合并；AGENTS.md 产品理念（八条）已由 lan 确认。下一张工单 006（工具调用）已写好 `docs/tickets/006-tools.md`，由 Codex 实现，Claude 评审。
- 006（工具调用）lan 2026-10-19 让 Codex 实现；调研文档是 Claude Doc「下一阶段可复用组件调查」。

## 下一个窗口第一步

先查看 PR #13 的 Windows CI 和小兔子评审；修复阻断问题后合并，再让 lan 按 STATUS 的 3 步做 Windows 假文件体验验收。工作区 `characters/peter/character.toml` 的 `thinking="enabled"` 是本机试验差异，未提交。lan 仍待验收 005c–005g。

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
