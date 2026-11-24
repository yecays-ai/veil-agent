# 当前状态

## P2（A/B/C）代码修复、隔离验证与端到端演练全部完成，待 Lan 体验验收与正式切换授权

- **P2-A**：Brain 独立写入权威、完整事件故事/阶段存储（69/69）、同 ID 导出与备份恢复通过。
  - **复查修复**：`hard_delete` 补齐清理 `sidecar_events` 与 `events.memory_id` 映射；
  - **真实模型隔离 E2E**：在独立子进程与独立端口上使用真实 DeepSeek 模型执行完整生命周期（启动→防注入写入→Brain召回→事件引用去重→多轮追问→进程退出与全新服务重启恢复），模块真正连通（`scripts/p2_isolated_peter_real_e2e.py`）。
- **P2-B**：读取接管、真实 Ombre 常驻回退演练已在独立进程跑通。
  - **30 条人工真值集**：建立含 24 条真实预期与 6 条负向无答案的真值问题集，评测报告已入库（Top-5 命中率 54.2%，Top-8 命中率 62.5%）。
  - **事件去重**：实测真实聊天检索中事件故事与旧碎片不发生重复注入（0 duplicate injections）。
  - **切换协议端到端演练**：启动真实 Ombre 常驻进程并执行完整密码学哈希对账（0 差异）、旧写冻结核验、差异中止保护与零数据丢失回退（`scripts/p2_cutover_drill_live.py`）。
- **P2-C**：标签膨胀源头治理与历史记忆核实。
  - **复查修复**：Brain 写入权威切断自由标签与 domain 生成，彻底阻断膨胀源头。
  - **分类结论**：明确七类固定分类不被采用，保持语义检索。
  - **145 条核实**：输出逐条清单，确认 140 条已自然覆盖，38 条建议保留独立，严禁盲目归组。
- **CI 与环境兼容性**：
  - 排查并修复了 Windows 上 `web_agent.py` 在 403 响应时未 drain request body 导致 `[WinError 10053]` 的网络异常。
  - 修复了 `brain_authority.py` 中 embedder 函数调用规范与 `brain_retrieval.py` 网络异常降级。
  - 63 条 Brain 单元测试与 73 条 Web 单元测试全部通过。

## 正式环境切换待办（严格等待 Lan 授权）

1. Lan 审核隔离验收证据（[`docs/tickets/p2/acceptance.md`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/acceptance.md)）。
2. Lan 安排维护窗口，执行正式切换协议（备份、冻结旧写、最终对账、开启 `write_authority=True`、重启 Peter）。
3. Lan 授权执行正式库标签清理（`tag_governance.py --write`）。
4. Lan 在真实 Peter 上体验保存、修改、回忆并确认 P2 验收通过，之后才进入 P3。
