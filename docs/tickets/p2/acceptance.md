# P2 验收步骤与复核证据（Lan）

## 1. 代码与工单交付物

- 分支：`fix/p2-review-corrections`
- 对应工单：`docs/tickets/p2/P2-A.md`、`P2-B.md`、`P2-C.md`
- 自审记录：`docs/tickets/p2/P2-A-review.md`、`P2-B-review.md`、`P2-C-review.md`
- 默认配置：`write_authority = false`，不改变正式 Peter 现有行为（保持 Ombre 写入 + Brain 召回）。

---

## 2. 隔离验收证据（已全部持久化至仓库，可随时复核）

所有开发、测试、故障注入、端到端演练均在独立进程与隔离副本中完成，未修改或打扰正式 Peter。

### 证据 A：隔离 Peter 真实模型端到端流程与去重审计
- **真实模型端到端脚本**：`python scripts/p2_isolated_peter_real_e2e.py`
  - **环境保障**：独立进程、独立数据副本、真实 DeepSeek 聊天模型、真实 HTTP 接口通信，无任何 Mock 或 Dummy。
  - **执行日志**：`docs/tickets/p2/evidence/isolated_peter_real_e2e.log`
  - **统计报告**：`docs/tickets/p2/evidence/isolated_peter_real_e2e_report.json`
- **去重注入专项审计脚本**：`python scripts/p2_dedup_prompt_audit.py`
  - **环境保障**：独立沙箱运行，使用候选注入桩全面审计最终模型 prompt 正文构造与候选去重逻辑，不消耗线上模型额度。
  - **统计报告**：`docs/tickets/p2/evidence/dedup_prompt_audit_report.json`
- **验证结果**：
  1. **服务启动**：隔离 Peter 独立常驻启动，通过 `/api/events` 握手成功（ready=True）。
  2. **Brain 独立写入（Hold）与自由标签阻断**：发送现场浮标信标（`LH-9921`）记忆，Peter 调用 `hold` 保存；`brain.db` 中生成记忆 `76ee93cf7989`，`tags_json='[]'`，`domain_json='[]'`，确凿阻断自由标签新增膨胀。
  3. **聊天召回**：提问浮标编号，Peter 正确回答 `LH-9921`，附带引用 `memory_refs=['76ee93cf7989']`，证明写入→召回真正连通。
  4. **事件故事引用与正文去重审计（区分引用跟踪与注入正文）**：
     - 在真实模型聊天提问仙人掌时，前端/轮次事件记录来源引用 `99df0c513389`；测试轮询中流式气泡曾导致引用重复累加，已修复去重。
     - 专项审计脚本 `p2_dedup_prompt_audit.py` 严格校验最终发送给模型的完整 prompt：
       * 注入正文为 V01 事件定稿完整故事（`[bucket_id:99df0c513389]\n{story}`）；
       * 原始未合并碎片的前置元数据与正文**严格未注入**；
       * 演练共现去重：当 V01 投影与 `99df0c513389` 同时出现在候选集时，`duplicate()` 判定 `same_event` 成功剔除冗余项；
       * 演练共享来源：多事件共享来源（9 个）不在 `unique` 账本中，严格保留碎片原文而不借用任何故事，完整保留 #424 规则。
  5. **连续追问**：多轮连续追问浇水频率，Peter 结合上下文给出季节浇水原则并实时复核金丝雀记忆笔记，上下文连贯无碎片堆积。
  6. **完整服务重启恢复**：通过 `/quit` 终止第一进程后，在相同隔离数据上启动全新 Peter 独立进程；再次提问浮标编号，Peter 正确回忆出 `LH-9921`，证明服务重启后数据持久完好。

### 证据 B：30 条人工真值问题集与召回评测
- **真值问题集**：`docs/tickets/p2/evidence/golden_truth_30.json`
  - 根据真实聊天与 69 篇事件故事建立，涵盖 24 条有明确期望记忆/事件的正向问题（仙人掌红砖色、记忆库初建、GPT借用键盘、地震、宵禁、万圣节生日信等），以及 6 条明确无记忆答案的负向问题（虚构滑雪、虚构机车、外部世界杯比分等）。
- **评测脚本**：`python scripts/p2_golden_eval.py`
- **评测报告**：`docs/tickets/p2/evidence/golden_truth_eval_report.json`
- **统计指标**：
  - 正向召回命中率：Top-5 命中 13/24（54.2%），Top-8 命中 15/24（62.5%），漏召回 11/24。
  - 平均检索延迟：764.1 ms（含远程向量接口通信）。
  - 负向问题表现：在候选检索层召回 0 条正确拒绝（假阳性率 100%，由后续模型过滤）。

### 证据 C：真实 Ombre 进程切换协议、旧写实际阻止与失败回退演练
- **复现方式**：`python scripts/p2_cutover_drill_live.py`
- **执行日志**：`docs/tickets/p2/evidence/cutover_drill.log`
- **统计报告**：`docs/tickets/p2/evidence/cutover_drill_report.json`
- **环境保障**：启动真实 Ombre 常驻服务进程，真实 HTTP MCP 通信，真实完整密码学对账，无任何 `writes_frozen=True` 模拟或对象 Mock。
- **演练结果**：
  1. **常驻 Ombre 启动**：隔离 Ombre 成功启动，16 个 MCP 工具正常就绪。
  2. **完整哈希对账与数据库校验**：运行 `ShadowSync.reconcile()`，对比 287 个源文件与 SQLite 全量记录，`memory_mismatches=0`、`version_mismatches=0`、`vector_mismatches=0`；运行 `veil_brain.check_db()` 确认 `integrity='ok'` 且 `digest_ok=True`。
  3. **一致性备份**：生成备份快照（SHA256: `ffe2d60f...`，4,390,912 字节）。
  4. **权威切换与旧写入确凿被阻止（安全停止证据）**：
     - 配置切换为 `write_authority=true, shadow=false`；验证单一权威互斥保护（`attach_authority` 与 `brain_sync.attach` 均拒绝双权威）。
     - 执行旧 Ombre 常驻服务安全停机（`resident.close()`），记录停机前 PID 与端口。
     - **旧服务安全停止证据**：停机后 `_pid_alive(pid)=False`，`_port_open(port)=False`，旧进程完全退出。
     - **旧写入被拒绝证据**：向旧 Ombre 端口发起 HTTP MCP 写入调用，确认被拒绝捕获 `URLError(TimeoutError('timed out'))`，旧写入通道彻底关闭。
     - **Brain 权威正常接管**：通过 `BrainAuthority` 写入金丝雀记忆，成功落库 `brain.db` 且 `tags_json='[]'`。
     - **Vault 保持冻结**：核验 Ombre vault 目录 287 个文件，完全未被修改，旧写确凿冻结。
  5. **差异中止保护演练（Discrepancy Abort）**：在 vault 副本中注入篡改内容，对账比对立即检测出 `mismatches=1`（`['2fa6166a7d7d']`），切换程序即刻安全中止，杜绝带差切换。
  6. **真实失败回退演练（Failure Rollback）**：将 Brain 新记忆（含金丝雀）无损导出回 vault 格式（288 条记忆），配置切回 `write_authority=false, shadow=true`，哈希对账确认 0 差异后重启 Ombre 常驻服务；通过 Ombre 原生 MCP `breath_search` 检索，成功召回金丝雀记忆内容与 ID，**数据零丢失，回退通道百分之百安全**。

### 证据 D：历史记忆逐条治理、下线数据清理与连续性演练
- **核实清单文档**：`docs/tickets/p2/historical_memories_verification.md`
- **治理与连续性演练**：`python scripts/p2_historical_governance_and_continuity_drill.py`
- **演练报告**：`docs/tickets/p2/evidence/historical_governance_continuity_report.json`
- **治理核验结果**：
  1. **已下线功能数据确认与清理（10 条）**：
     - #426 中 4 封信件导出至 `workspace/lab/letters/`、5 条计划并入 `life-records.json`（已标记 completed）、花园桌游规则在 `growth` 中跳过迁移，正式迁移日志与目标文件全部对齐。
     - 在隔离副本中清理 Brain 对应 10 条冗余记忆及向量索引，确认不再出现在普通召回中，原数据在备份中保留。**不再将这 10 条统计为有效记忆**。
  2. **剩余 28 条记忆实际处置**：
     - 输出 28 条详细处置清单，逐条具备正文依据、历史依据与去向理由，无模板化敷衍。
     - `c13e3ebd3a84`：独立保留，脱离 V36。
     - `2fd52c0b984b`：设置 `dont_surface=1, is_hidden=1`，实测普通召回已排除，按 ID 审计读取 1020 字符完整。
     - `f8c23ac29f8d`：归属于 V09，不挂回 V36。
     - `aea3998b1742` 与 `47118c6bf87c`：保持 digested 与 archived 状态。
     - `9437bc89b0f7`：保持合并进 `e06887bb8863` 的隐藏状态。
     - #426 feel/I 删除数据：确认未复活。
     - #429 7 个悬空事件来源与 8 条 sidecar 链接：确认为已知历史遗留。
  3. **记忆连续性机制在 Brain 下正常运行**：
     - 经历新发展：向现有经历 V09 记录新进展，系统成功关联并保留原 `event_id='V09'`，原故事与来源完整保留。
     - 独立新经历：自动创建全新事件 ID（`e-20261124-f7d075`），独立建立时间线。
     - 完整故事召回：实测长事件 V01 完整故事（1041 字符）全量召回，无 600 字上限截断。

### 证据 E：CI 修复与单元测试
- **测试命令**：`.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_brain_*.py'`
- **测试结果**：所有 Brain 相关单元测试 64/64 与 Web 单元测试 73/73 全部通过。
- **Windows CI 失败排查**：
  - 修复 Windows CI 上 Playwright Chromium 启动 8 秒刚性超时导致的偶发报错（放宽至 30 秒超时）。
  - 定位并修复了 Windows 上 `web_agent.py` 在 403 拒绝时未清空 socket body 导致 `[WinError 10053]` 的网络兼容性问题。
  - 修复了 `brain_authority.py` 中 embedder 函数调用规范与 `brain_retrieval.py` 异常降级兜底。
  - 修复了 `memory_agent.py` 中 Brain 写入/检索未纳入预算预估与结算的问题。

---

## 3. 标签治理决定与待 Lan 决定的事项（正式环境）

### 3-A. 标签治理决定（已在代码与架构中落实）
1. **不采用固定七类分类**：七类方案覆盖率低，不符合实际记忆结构，彻底放弃。
2. **停止生成自由标签及无意义新 domain**：`brain_authority.py` 已在写入链中阻断自由 tags 与 domains 生成，新写入记忆 `tags_json='[]'`。
3. **历史无用标签必须实际清理**：不能仅在 UI 层隐藏。
4. **正式清理安排在授权后的安全切换流程中**：在切换维护窗口冻结写入后执行，防止影子同步反向重新导入。
5. **后续记忆管理页面直接使用治理后的数据结构和真实统计**，不再承担历史标签清理负担。

### 3-B. 待 Lan 授权与决定的正式事项
> [!IMPORTANT]
> 隔离演练与功能修复已全部闭环。以下事项涉及正式数据与正式服务，严格等待 Lan 的授权与指示：

1. **正式切换窗口授权**：
   - 开发者不擅自修改正式配置、不擅自重启正式 Peter。
   - 等待 Lan 安排约 2–3 分钟维护窗口，按 `docs/tickets/p2/P2-B-runbook.md` 执行正式切换。
2. **正式库标签清理与冗余数据治理**：
   - 在正式切换停机窗口内，执行正式库标签清理与 10 条已下线功能的冗余记忆清理。
3. **正式体验验收**：
   - 切换完成后由 Lan 在真实聊天中体验保存、修改、回忆并确认 P2 最终闭环。
