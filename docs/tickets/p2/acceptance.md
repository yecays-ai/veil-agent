# P2 验收步骤与复核证据（Lan）

## 1. 代码与工单交付物

- 分支：`fix/p2-review-corrections`
- 对应工单：`docs/tickets/p2/P2-A.md`、`P2-B.md`、`P2-C.md`
- 自审记录：`docs/tickets/p2/P2-A-review.md`、`P2-B-review.md`、`P2-C-review.md`
- 默认配置：`write_authority = false`，不改变正式 Peter 现有行为（保持 Ombre 写入 + Brain 召回）。

---

## 2. 隔离验收证据（已全部持久化至仓库，可随时复核）

所有开发、测试、故障注入、端到端演练均在独立进程与隔离副本中完成，未修改或打扰正式 Peter。

### 证据 A：隔离 Peter 完整端到端流程与事件去重（工单 424）
- **复现方式**：`python scripts/p2_isolated_peter_e2e.py`
- **执行日志**：[`docs/tickets/p2/evidence/isolated_peter_e2e.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/isolated_peter_e2e.log)
- **统计报告**：[`docs/tickets/p2/evidence/isolated_peter_e2e_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/isolated_peter_e2e_report.json)
- **验证结果**：
  1. **Brain 独立写入**：通过 `Memory.call('hold')` 在 `write_authority=True` 下成功写入记忆（`新建→...`）；写入时传入的自由 tags/domain 被底层确凿丢弃，`tags_json='[]'`，彻底阻断自由标签新增膨胀。
  2. **聊天召回**：通过 `breath_search` 成功检索到新写入的记忆。
  3. **事件故事与旧碎片去重**：真实检索仙人掌经历时，V01 事件故事正常召回，其关联的旧碎片（`99df0c513389`）被 `_event_ledger()` 成功折叠去重，**0 重复注入**。
  4. **连续追问**：多轮对话上下文连贯，无碎片冗余堆积。
  5. **重启恢复**：销毁 `Memory` 实例后重新从 `brain.db` 加载，开场白（`breath`）与检索功能完好，`PRAGMA integrity_check` 结果为 `ok`。

### 证据 B：30 条人工真值问题集与召回评测
- **真值问题集**：[`docs/tickets/p2/evidence/golden_truth_30.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/golden_truth_30.json)
  - 根据真实聊天与 69 篇事件故事建立，涵盖 24 条有明确期望记忆/事件的正向问题（仙人掌红砖色、记忆库初建、GPT借用键盘、地震、宵禁、万圣节生日信等），以及 6 条明确无记忆答案的负向问题（虚构滑雪、虚构机车、外部世界杯比分等）。
- **评测脚本**：`python scripts/p2_golden_eval.py`
- **评测报告**：[`docs/tickets/p2/evidence/golden_truth_eval_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/golden_truth_eval_report.json)
- **统计指标**：
  - 正向召回命中率：Top-5 命中 13/24（54.2%），Top-8 命中 15/24（62.5%）。
  - 平均检索延迟：833.3 ms（含远程向量接口通信）。
  - 漏召回分析：未命中项多为中文提问对英文故事标题的语义跨度，依赖向量相似度排序，可通过召回限额与判官层进一步提升。

### 证据 C：切换协议端到端演练（正常流程与差异中止）
- **复现方式**：`python scripts/p2_cutover_drill.py`
- **执行日志**：[`docs/tickets/p2/evidence/cutover_drill.log`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill.log)
- **统计报告**：[`docs/tickets/p2/evidence/cutover_drill_report.json`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/evidence/cutover_drill_report.json)
- **演练结果**：
  - **Happy Path**：一致性备份 → 模拟冻结写入 → 最终对账（Brain 286 条 vs Vault 286 文件，diff=0）→ 锁定单一权威（`write_authority=True`, `shadow=False`）→ 切换并验证读写成功。
  - **Failure Injection Path**：在对账前故意注入 1 条孤儿 Markdown 文件，对账脚本检出 `diff=1 > 0`，**触发紧急中止（ABORT）**，保持 `write_authority=False`，成功阻止双写。

### 证据 D：145 条历史记忆核实清单
- **核实清单文档**：[`docs/tickets/p2/historical_memories_verification.md`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/historical_memories_verification.md)
- **核实结论**：
  - 正式库 286 条记忆中，69 条为事件故事投影。
  - 217 条非事件记忆中，**140 条（64.5%）已作为事件来源被 69 篇事件无损引用**。
  - 剩余未引用记忆中：39 条为历史墓碑/软删除记忆（严禁复活），**38 条为独立有效记忆**。
  - 清单对 38 条独立有效记忆给出了逐条处置建议（主要是作为独立性格或偏好保留独立检索，禁止盲目塞进现有事件）。

### 证据 E：CI 修复与单元测试
- **测试命令**：`.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_brain_*.py'`
- **测试结果**：所有 Brain 相关单元测试 22/22 全部通过。
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
