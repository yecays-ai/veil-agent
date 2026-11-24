# HANDOFF

## P2 复查与隔离验证收尾（2026-11-24）

- 分支：`fix/p2-review-corrections`
- 修复内容：
  1. `brain_authority.py`：切断 `_hold` 写入自由 tags / domain 的路径；修复 `breath_search` 的 embedder 调用规范；
  2. `brain_writer.py`：`hard_delete` 补齐清除 `sidecar_events` 与断开 `events.memory_id` 映射；
  3. `web_agent.py` & `test_web.py`：解决 Windows CI 上 403 触发 `[WinError 10053]` 的网络异常；
  4. `brain_retrieval.py`：增加 embedder 网络异常的安全降级；
  5. 交付持久化证据：
     - 30 条人工真值集：`docs/tickets/p2/evidence/golden_truth_30.json`
     - 真值召回评测报告：`docs/tickets/p2/evidence/golden_truth_eval_report.json`
     - 隔离 Peter E2E 完整日志：`docs/tickets/p2/evidence/isolated_peter_e2e.log`
     - 切换协议正常与中止演练日志：`docs/tickets/p2/evidence/cutover_drill.log`
     - 145 条历史记忆与 69 篇事件核实报告：`docs/tickets/p2/historical_memories_verification.md`

## 待 Lan 决定的正式环境事项

1. **正式切换窗口**：按 `docs/tickets/p2/P2-B-runbook.md` 执行正式备份、冻结旧写、最终对账（0 差异）、切换单一权威。
2. **正式标签清理**：经 Lan 授权后，在正式库执行 `python tag_governance.py apply --db data/peter/brain.db --write`。
3. **145 条历史记忆**：确认 38 条独立有效记忆保持独立检索，不执行盲目重归组。
4. **真实体验验收**：Lan 在真实 Peter 上体验保存/修改/回忆，通过后正式关闭 P2，进入 P3。

---

## 历史交接信息与背景（保留供追溯）

### P1 试运行状态（工单 433，`f2f68c7`）
- `characters/peter/character.toml` 的 `backend = "brain"` 处于开启状态。
- 写入权威仍是 Ombre，影子同步运行中；Brain 只读召回。
- 回退方式：改 `backend = "ombre"` 并重启 VEIL，不动 `brain.db` 与 Ombre 数据。

### 工单 425 交接（经历页 GET 路由）
- `web_agent.py` GET 分支支持 `/api/memory/events`（列表）与 `/api/memory/event`（详情），73 条 test_web 测试通过。

### 工单 424 交接（事件/碎片不重复注入）
- `memory_agent.py` 的 `_event_ledger()` 负责事件与碎片的同次回忆去重，已通过端到端脚本二次验证。

### 工单 428 / 429 / 430（P0-A / P0-B / P1 基础）
- `veil_brain.py`、`brain_sync.py`、`brain_retrieval.py` 构成了 Brain 底层核心。
