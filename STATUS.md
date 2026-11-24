# 当前状态

## P2（A/B/C）代码修复、隔离验收与治理方案已全部完成；待 Lan 审核、正式维护切换授权与真实体验验收（P2 尚未关闭）

- **P2-A（写入权威与核心存储）**：
  - Brain 独立写入权威、完整事件故事/阶段存储（69/69）、同 ID 导出与备份恢复通过。
  - **复查修复**：`hard_delete` 补齐清理 `sidecar_events` 与 `events.memory_id` 映射，最小 tombstone 阻止回退复活。
  - **真实模型隔离 E2E**：在独立子进程与独立端口上使用真实 DeepSeek 模型执行完整生命周期（启动→防注入写入→Brain 召回→事件引用去重→多轮追问→进程退出与全新服务重启恢复），证明各模块真正连通（证据：`docs/tickets/p2/evidence/isolated_peter_real_e2e_report.json`）。
- **P2-B（读取接管、去重审计与切换演练）**：
  - **30 条人工真值集**：建立含 24 条明确预期记忆与 6 条负向无答案的真值问题集，真实评测报告已入库（24 条正向问题中 13 条命中 Top-5，召回率 54.2%，11 条漏召回；6 条负向问题在候选检索层召回 0 条正确拒绝，假阳性率 100%，由后续模型过滤；`docs/tickets/p2/evidence/golden_truth_eval_report.json`）。
  - **事件与碎片去重正文注入审计**：
    - 查清并修复了 streaming 轮询多气泡导致 `memory_refs` 累加旧碎片引用的问题；
    - 严格区分来源引用追踪（`memory_refs`）与模型正文注入（`prompt`）：证实唯一来源折叠为事件故事正文，旧碎片原文 100% 不重复注入；多来源碎片保留原文绝不借用任意故事，0 重复注入（证据：`docs/tickets/p2/evidence/dedup_prompt_audit_report.json`）。
  - **真实常驻 Ombre 写入切断与切换演练**：
    - 在独立进程启动常驻 Ombre，执行完整密码学哈希对账（0 差异）；
    - 真实执行旧常驻服务安全停止（`resident.close()`），核验证实端口关闭、向旧 Ombre 端口直接写入被物理拒绝（`URLError: timed out`）；
    - 证明权威切换至 Brain 后，新写入 canary 记录落盘（0 自由 tags），旧 Ombre 库（287 文件）100% 保持未修改；
    - 演练回退干净恢复常驻 Ombre 并查出 canary 记录，零数据丢失（证据：`docs/tickets/p2/evidence/cutover_drill_report.json`）。
- **P2-C（标签膨胀源头治理、历史记忆处置与连续性验收）**：
  - **标签膨胀源头阻断**：Brain 写入权威接管时强制将 `tags` 和 `domain` 置为 `[]`，彻底阻断自由标签与碎片 domain 膨胀源头（已通过单测与真实 E2E 验证）。
  - **标签分类决定**：明确审计确认固定七类分类方案（63.2% 未分类）不被采用；历史无用标签必须实际清理，安排在正式切换窗口执行；后续记忆管理页面直接使用治理后数据结构。
  - **已下线功能遗留数据治理（10 条）**：
    - 核对 4 封信件（已在 `workspace/lab/letters/`）与 5 条计划（已迁入 `data/peter/life-records.json` 且标记完成），1 条桌游规则已在 `growth/`；
    - 在隔离 Brain 中彻底清理了对应的冗余记录与检索向量，确认不重复迁移、不重复导出日历/信件，坚决不再计为待归组的有效记忆。
  - **剩余 28 条有效历史记忆实际处置**：
    - 逐条读取真实正文，对照聊天记录、69 篇事件故事与六批整理记录，杜绝模板化理由，完成 28 条逐条处置（22 条独立保留、2 条归入事件 V09/V19、4 条隐藏/归档）；
    - 严格遵守既定决定（`c13e3ebd3a84` 独立保留、`2fd52c0b984b` 隐藏普通召回但支持 ID 查、`f8c23ac29f8d` 归 V09 不回 V36、`feel/I` 不复活等；详见 `docs/tickets/p2/historical_memories_verification.md`）。
  - **记忆连续性接管演练**：
    - 同一经历新发展命中并续写 V09（增加 stage，保留 event_id、原话与锁定保护）；独立经历新建事件；完整故事 1041 字符无损召回（无 600 字上限；证据：`docs/tickets/p2/evidence/historical_governance_continuity_report.json`）。
- **CI 与环境兼容性**：
  - 修复 Windows CI 上 Playwright Chromium 启动 8 秒刚性超时导致的偶发报错（放宽至 30 秒超时）。
  - 修复 Windows 上 `web_agent.py` 在 403 响应时未 drain request body 导致 `[WinError 10053]` 的网络异常。
  - 修复 `memory_agent.py` 中 Brain 写入/检索未纳入预算预估与结算的问题。
  - 64 条 Brain 单元测试与 73 条 Web 单元测试全部通过。

## 正式环境切换待办（严格等待 Lan 授权，禁止擅自操作正式库）

1. **审核与授权**：Lan 审核隔离验收证据（[`docs/tickets/p2/acceptance.md`](docs/tickets/p2/acceptance.md)）与 28 条历史记忆处置清单。
2. **正式切换窗口**：Lan 安排维护窗口，按 `docs/tickets/p2/P2-B-runbook.md` 执行正式备份、停写 Ombre、最终对账（0 差异）、切换单一写入权威、重启正式 Peter。
3. **正式数据物理清理**：在正式维护窗口执行历史标签清理（`tag_governance.py --write`）与 10 条已下线功能遗留记忆清理。
4. **真实体验验收**：Lan 在真实 Peter 上体验保存、修改、回忆、连续追问，体验验收通过后正式关闭 P2，进入 P3（下线 Ombre，推进记忆管理页面）。
