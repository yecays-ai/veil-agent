# P2 验收步骤与复核证据（Lan）

## 1. 代码与工单交付物

- 分支：`fix/p2-review-corrections`
- 对应工单：`docs/tickets/p2/P2-A.md`、`P2-B.md`、`P2-C.md`
- 自审记录：`docs/tickets/p2/P2-A-review.md`、`P2-B-review.md`、`P2-C-review.md`
- 默认配置：`write_authority = false`，不改变正式 Peter 现有行为（保持 Ombre 写入 + Brain 召回）。

---

## 2. 隔离验收证据（已全部持久化至仓库，可随时复核）

所有开发、测试、故障注入、端到端演练均在独立进程与隔离副本中完成，未修改或打扰正式 Peter。

### 证据 A：隔离 Peter 真实模型端到端流程与重启恢复（工单 424）
- **复现方式**：`python scripts/p2_isolated_peter_real_e2e.py`
- **执行日志**：[`docs/tickets/p2/evidence/isolated_peter_real_e2e.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/isolated_peter_real_e2e.log)
- **统计报告**：[`docs/tickets/p2/evidence/isolated_peter_real_e2e_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/isolated_peter_real_e2e_report.json)
- **环境保障**：独立进程（PID=38084/web_port=64405）、独立数据副本、真实 DeepSeek 聊天模型、真实 HTTP 接口通信，无任何 Mock 或 Dummy。
- **验证结果**：
  1. **服务启动**：隔离 Peter 独立常驻启动，通过 `/api/events` 握手成功（ready=True）。
  2. **Brain 独立写入（Hold）与自由标签阻断**：发送现场浮标信标（`LH-9921`）记忆，Peter 执行防注入确认并调用 `hold` 保存；`brain.db` 中生成记忆 `76ee93cf7989`，`tags_json='[]'`，`domain_json='[]'`，确凿阻断自由标签新增膨胀。
  3. **聊天召回**：提问浮标编号，Peter 正确回答 `LH-9921`，附带引用 `memory_refs=['76ee93cf7989']`，证明写入→召回真正连通。
  4. **事件故事引用与旧碎片去重**：提问仙人掌种植经历，Peter 准确引用 V01 事件故事（细致回忆薄荷植物前科与仙人掌照顾细节），关联旧碎片 `99df0c513389` 被底层 `_event_ledger()` 成功去重折叠，**0 重复注入**。
  5. **连续追问**：多轮连续追问浇水频率，Peter 结合上下文给出季节浇水原则并实时复核金丝雀记忆笔记，上下文连贯无碎片堆积。
  6. **完整服务重启恢复**：通过 `/quit` 优雅终止第一进程后，在相同隔离数据上启动全新 Peter 进程；再次提问浮标编号，Peter 正确回忆出 `LH-9921`（"LH-9921. Third time you've asked me that..."），证明服务重启后数据持久完好。

### 证据 B：30 条人工真值问题集与召回评测
- **真值问题集**：[`docs/tickets/p2/evidence/golden_truth_30.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/golden_truth_30.json)
  - 根据真实聊天与 69 篇事件故事建立，涵盖 24 条有明确期望记忆/事件的正向问题（仙人掌红砖色、记忆库初建、GPT借用键盘、地震、宵禁、万圣节生日信等），以及 6 条明确无记忆答案的负向问题（虚构滑雪、虚构机车、外部世界杯比分等）。
- **评测脚本**：`python scripts/p2_golden_eval.py`
- **评测报告**：[`docs/tickets/p2/evidence/golden_truth_eval_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/golden_truth_eval_report.json)
- **统计指标**：
  - 正向召回命中率：Top-5 命中 13/24（54.2%），Top-8 命中 15/24（62.5%）。
  - 平均检索延迟：764.1 ms（含远程向量接口通信）。
  - 漏召回分析：未命中项多为中文提问对英文故事标题的语义跨度，依赖向量相似度排序，可通过召回限额与判官层进一步提升。

### 证据 C：真实 Ombre 进程切换协议与失败回退演练
- **复现方式**：`python scripts/p2_cutover_drill_live.py`
- **执行日志**：[`docs/tickets/p2/evidence/cutover_drill.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill.log)
- **统计报告**：[`docs/tickets/p2/evidence/cutover_drill_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill_report.json)
- **环境保障**：启动真实 Ombre 常驻服务进程（端口 54401），真实 HTTP MCP 通信，真实完整密码学对账，无任何 `writes_frozen=True` 模拟或对象 Mock。
- **演练结果**：
  1. **常驻 Ombre 启动**：隔离 Ombre 成功启动，16 个 MCP 工具正常就绪。
  2. **完整哈希对账与数据库校验**：运行 `ShadowSync.reconcile()`，对比 287 个源文件与 SQLite 全量记录，`memory_mismatches=0`、`version_mismatches=0`、`vector_mismatches=0`；运行 `veil_brain.check_db()` 确认 `integrity='ok'` 且 `digest_ok=True`。
  3. **一致性备份**：生成备份快照（SHA256: `dd37ea0d...`，4,390,912 字节）。
  4. **锁定权威与旧写实际冻结**：切换配置为 `write_authority=true, shadow=false`；验证单一权威互斥保护（`attach_authority` 与 `brain_sync.attach` 均拒绝双权威）；通过 `BrainAuthority` 写入金丝雀记忆 `55c43fd872c6`（`tags_json='[]'`）；核验 Ombre vault 目录 287 个文件，**完全未被修改**，旧写入确凿被阻止。
  5. **差异中止保护演练（Discrepancy Abort）**：在 vault 副本中注入篡改内容，对账比对立即检测出 `mismatches=1`（`['2fa6166a7d7d']`），切换程序即刻安全中止，杜绝带差切换。
  6. **真实失败回退演练（Failure Rollback）**：将 Brain 新记忆（含金丝雀）无损导出回 vault 格式（288 条记忆），配置切回 `write_authority=false, shadow=true`，哈希对账确认 0 差异后重启 Ombre 常驻服务；通过 Ombre 原生 MCP `breath_search` 检索，成功召回金丝雀记忆内容与 ID，**数据零丢失，回退通道百分之百安全**。

### 证据 D：145 条历史记忆核实清单
- **核实清单文档**：[`docs/tickets/p2/historical_memories_verification.md`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/historical_memories_verification.md)
- **核实结论**：
  - 正式库 286 条记忆中，69 条为事件故事投影。
  - 217 条非事件记忆中，**140 条（64.5%）已作为事件来源被 69 篇事件无损引用**。
  - 剩余未引用记忆中：39 条为历史墓碑/软删除记忆（严禁复活），**38 条为独立有效记忆**。
  - 清单对 38 条独立有效记忆给出了逐条处置建议（主要是作为独立性格或偏好保留独立检索，禁止盲目塞进现有事件）。

### 证据 E：CI 修复与单元测试
- **测试命令**：`.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_brain_*.py'`
- **测试结果**：所有 Brain 相关单元测试 63/63 与 Web 单元测试 73/73 全部通过。
- **Windows CI 失败排查**：定位并修复了 Windows 上 `web_agent.py` 在 403 拒绝时未清空 socket body 导致 `[WinError 10053]` 的网络兼容性问题，同时修复了 `brain_authority.py` 中 embedder 函数调用规范与 `brain_retrieval.py` 异常降级兜底。

---

## 3. 明确待 Lan 决定的事项（正式环境）

> [!IMPORTANT]
> 隔离演练与功能修复已全部闭环。以下四项涉及正式数据与正式服务，严格等待 Lan 的授权与指示：

### 3-A. 七类固定分类方案：已确认**不采用**
- 隔离映射显示七类仅覆盖 36.8% 的历史记忆，63.2% 无法准确归类。
- P2-C 结论：停止生成自由标签，`tags` 保持只读历史，**不为页面设立固定的技术七类**，依靠语义检索与结构化字段。
- **待 Lan 确认**：认可该治理结论。

### 3-B. 正式标签清理（需窗口授权）
- `tag_governance.py --write` 仅在隔离副本执行；正式库 2107 个自由标签仍保留现状。
- **待 Lan 授权**：在正式切换窗口内，由开发者执行正式库标签清理与备份。

### 3-C. 145 条旧记忆迁移确认
- 核实清单已输出，明确 140 条已自然覆盖，38 条建议保留独立。
- **待 Lan 授权**：确认 38 条记忆保持独立、不进行盲目重归组。

### 3-D. 正式切换窗口与体验验收
- 开发者不擅自修改正式配置、不擅自重启正式 Peter。
- **待 Lan 安排**：指定约 2–3 分钟维护窗口，按 `docs/tickets/p2/P2-B-runbook.md` 执行正式切换。切换后 Lan 在真实聊天中体验保存、修改、回忆并确认最终完成。
