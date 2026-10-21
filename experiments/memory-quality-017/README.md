# 工单 017 实验夹具

这里的内容是虚构数据，不来自 Peter/Ghost 真实记忆或聊天。`transcripts.jsonl` 是冻结后的共同输入，`questions.jsonl` 是事先固定的盲测与证据表。不要把 `facts`、`evidence_seq` 或预期答案传给任何记忆保存、整理或召回过程；评分时才读取。

## 固定源

- Ombre：复用 VEIL `memory_agent.py`、`memory_native.py`、`veil_agent.py` 的原生 `hold` / `breath_search` 及 `tests/test_memory_live.py` 接入边界；上游版本固定见 `docs/ombre-005-compatibility.md` 的 commit `115d831d979bf67efc7150f976e2cb34c333c935`。
- Serein：未修改的上游 `Yinglianchun/Serein` commit `6b4ee499d3efc69ad646783c069c95f4dc24e438`。使用其原话入库、自动 Event 整理、主模型 Scene 写入、普通召回与 `read_memory(with_evidence=True)`；上游 pytest 作安装/行为核对。不开不相关的 Arc、日记、后台梦境等功能。
- 上游提交、运行配置、自动生成内容、调用账本和最终报告不混放。正式记忆绑定、角色人设和 016 lab 都不作输入。

Ombre 使用 VEIL 当前的 `ombre_resident.start_or_connect()` 在实验专用 vault 下启动，
再由 `memory_agent.MCP` 连接 `http://127.0.0.1:<随机端口>/mcp`。端口只监听回环，
每次启动使用独立 Bearer token，不连接 Peter/Ghost 的正式常驻实例。

## 样例覆盖

Peter 的 11 个对话片段串成一件经历：想重走与父亲等船的旧港路线；渡船停航后争执；Peter 道歉、用户情绪缓和；双方约定先听感受，再决定路线；馆方后来更正展览结束日和入口地址。其间插入一餐午饭和浇花提醒。

Ghost 的 5 个片段记录天文夜日期/地点/任务的修正、青绿色偏好及外婆家缘由、灯罩的小习惯和一次无关的香蕉闲聊。Ghost 与 Peter 使用完全分离的库。

`questions.jsonl` 含 11 条盲测：事件原因与过程、争执和修复、承诺、事实更新、一次闲聊的保存倾向、逐字证据、两种角色串线和无法由样例支持的问题。评分前不把标准答案暴露给运行器。

`prepare_serein_imports.py` 只从聊天夹具生成上游通用对话导入格式，按 Peter/Ghost 拆分成独立会话 ID，并按固定规则生成时间戳；不读取盲测/标准答案、不写 Scene/Event、不触发任何模型。输出只写入 `.local-tmp/experiment-017/serein-imports/`，再由实验运行器调用上游导入端点。

## 费用闸门

`budget_proxy.py` 只绑定 loopback，并且仅转发实验指定的 DeepSeek chat-completions 与 SiliconFlow embedding/rerank 端点。计费请求在联网前先把最坏金额和请求编号追加到账本；账本位于用户指定的忽略目录 `.local-tmp/`，不含请求正文、响应正文、密钥或人设。请求失败、响应中断或供应商未返回 token 用量时，保留该请求的最高预留，不释放额度。进程重启会从账本还原累计消费和未完成预留；同一账本加独占锁，避免并发代理绕过上限。

DeepSeek 当前配置价格按官方高峰值核算；每次计费请求最多 60,000 请求体字节、预留另加 5,000 输入 token、输出最多 8,000 token，最多 120 次；每次最坏预留 ¥0.194，整场硬上限 ¥9.99，极端情况下最多允许 51 次最坏请求。Serein 实测 track-router 请求约 19.5 KB、Event curator 约 45.6 KB；输出 2,000 与 4,000 token 时模型回复都因输出预算耗尽而被截断，因此闸门提至 8,000，且不改模型默认推理模式。任一大于 60 KB 的输入仍会在联网前拒绝。预留按未缓存高峰价计算，不扣缓存折扣；实际结算以模型返回的 usage 为准。BAAI/bge-m3 embedding 和 BAAI/bge-reranker-v2-m3 reranker 按 SiliconFlow 当前价为免费模型，本代理另限 1,200 次并记调用数。正式运行只用隔离实例；任何收费请求发现绕过代理都必须中止。

本次 Windows 端到端运行已完成，用户可读结果见 `docs/reports/017-serein-vs-ombre.md`。Serein/Ombre 均使用隔离实例并经过同一本机预算账本；实际用量为 43 次付费请求、¥0.40328376，以及 1,061 次免费 embedding/rerank。脚本 `--fresh` 可清理本实验记忆库和结果后复跑，但会再次产生费用；保留的累计账本仍执行请求前预留、¥9.99/120 次付费请求和 1,200 次免费调用硬限制。

HTTP 适配回归使用虚构 token、假连接器和临时 vault，可运行 `python -m unittest discover -s tests -p test_experiment_017_ombre_http.py`；它不读取 `.env`、不发起模型供应商请求，也不会触碰角色正式记忆。`run_experiment.py --preflight-ombre` 会启动两个空的实验 vault 检查本机 HTTP/MCP 握手，不发送模型请求，但需要固定 Ombre 运行时已安装。完整质量复跑会向模型发送虚构实验内容并产生真实费用，仍受累计账本硬上限保护。


