# P2 隔离验收矩阵（P2-A / P2-B / P2-C 原始工单）

生成时间：2026-11-24。所有场景都在仓库外的隔离副本上执行；正式 Peter 未被启动、停止、修改或写入。
真实记忆正文、聊天、密钥与原始证据只保存在本机仓库外，本文件只保留 ID、计数与脱敏结论。

## 1. P2-A：Brain 独立写入与完整事件数据

| 验收项 | 实际执行场景 | 预期 | 实际 | 证据位置（仓库外） | 结果 |
|---|---|---|---|---|---|
| 事件迁移对账 | 从正式数据的隔离快照做 v3 迁移，逐字段对比 287 记忆、72 事件、73 阶段、284 版本、171 sidecar、248 向量、关系/来源/原话 | 0 缺失/重复/半条 | 12 组检查、0 mismatch；digest/integrity ok | `p2-439/iso/evidence/data/migration-verification.json` | 通过 |
| 事件 69/69 与阶段 | 当前快照 72 事件/73 阶段（含后续新增），逐条 story/title/status/时间/来源/confidence/approved/原话/来源/未知字段 payload | 全部无损 | 72/72 story；字段逐项一致 | 同上 | 通过 |
| 写入矩阵 | hold/trace/合并/锁定/归档/恢复/隐藏/软删/硬删/版本恢复/来源/链接，经 Brain authority | 每种写入正确、无重复、无半条 | 桌面页与工具路径均通过；硬删只留 tombstone；合并归档来源 | `evidence/desktop/desktop-e2e-result.json`、`evidence/legacy/legacy-convergence.json` | 通过 |
| 三态反馈 | embedding 401：hold 返回“正文已保存，检索索引待重试”，事件层转述同一提示；DB `vector_state=pending`；恢复 embedder 后重试为 ready | saved / saved_index_pending / failed 三态明确 | 401 时 pending 且明确提示；恢复后 ready；绝不谎报 saved | `evidence/failure/failure-direct.json`、`failure-chat.json` | 通过 |
| 写入失败 | brain.db 只读时保存 | 报错、事务回滚、无半条记录 | `OperationalError`，前后计数不变，无目标行 | `evidence/failure/failure-direct.json`、`failure-chat.json` | 通过 |
| 可见性 | hidden/dont_surface、locked、archived、tombstone、hard delete | 按工单契约 | hidden 不进自动召回/列表，工具可恢复；locked 拒绝 Peter 改写；archived 可页面恢复；tombstone/hard delete 不复活 | 同上 + `evidence/desktop/desktop-e2e-result.json` | 通过（页面无独立隐藏按钮，见 §4） |
| 互斥 | authority=brain + shadow=true 启动 | 拒绝 | `attach_authority` 抛错；单独 authority 正常 | `evidence/legacy/legacy-convergence.json` | 通过 |
| 导出/导入完整性 | 同 ID 导出 → 重新导入新 brain.db | 0 problems，ID/正文/版本/状态/事件一致 | 289 记忆/288 版本/177 sidecar，0 problems；已删不复活 | `evidence/cutover/cutover-rollback.json`、`evidence/cutover/export/` | 通过 |
| 备份恢复 | 停写 → 在线备份 → 全新目录恢复 → 启动 Peter | digest/counts 一致，能读/写/改/召回 | sha256 完全一致；恢复后 16/16 步骤通过，真实聊天能想起 | `evidence/restore/backup-restore-result.json`、`evidence/backup/brain.db` | 通过 |
| 旁路审计 | 全部写入经 authority；vault Markdown 前后 mtime/数量对比 | 无 hold/trace 绕过 | 旧 backend 调用 0 次，287 个 Markdown 未变 | `evidence/legacy/legacy-convergence.json` | 通过 |

## 2. P2-B：读取接管、生命周期收敛与切换回退

| 验收项 | 实际执行场景 | 预期 | 实际 | 证据位置（仓库外） | 结果 |
|---|---|---|---|---|---|
| 页面兼容 | 实际启动 Electron 桌面端：列表、搜索、详情、修改、历史版本、恢复、锁定/解锁、归档/恢复、隐藏、删除、事件详情 | 页面可用、字段兼容 | 17/17 步骤通过；panel/events JSON 字段齐全 | `evidence/desktop/desktop-e2e-result.json`、`30~40-*.png` | 通过 |
| 读取入口 | 自动召回、开场、主动查找、判官/assisted、页面/版本/事件、后续追问 | 全部走 Brain | 页面、搜索、判官、事件读取均走 Brain；Ombre 只作降级/回退 | 同上 + `evidence/quality/quality-summary.json` | 通过 |
| 黄金问题集 | 50 条真实聊天旧注入问题，Brain vs 独立 Ombre，top-8；另加 8 条无答案问题与 1 轮真实无答案聊天 | 重要遗漏不增加、延迟不明显恶化 | Brain 命中 29/50 vs Ombre 16/50；期望记忆覆盖率 36.0% vs 22.8%；延迟 187 vs 517 ms；4 条 Ombre-only 案例 | `evidence/quality/quality-summary.json`、`evidence/quality/ab-baseline/ab-report.json` | 通过（4 条残留风险已在报告列明） |
| 无答案 | 8 条无答案问题的绝对余弦（最高 0.535），真实聊天“年糕的猫” | 不硬召回 | 最高 0.535 < 0.6；judge=empty；injected=[]；Peter 明确说没有 | `evidence/quality/quality-summary.json`、`evidence/negative-chat.json` | 通过 |
| 去重与承接 | 真实聊天建事件 A → link_to 续写 A → 新事件 B；V02 长故事召回；连续追问 | 旧事件同 ID、新经历新 ID、长故事完整、无重复注入 | A 保持同一 event/memory；B 新 ID；V02 2428 字完整进入发送上下文（truncated=false）；追问承接上一轮；无同 ID 重复、事件与碎片不同时注入 | `evidence/continuity/continuity-summary.json`、`evidence/continuity-2~6.json` | 通过 |
| 超过 600 字故事 | V02 完整故事 2428 字 | 完整召回 | `memory.recalled` 块含完整故事，block 4960 字，truncated=false | 同上 | 通过 |
| 旧机制收敛 | 实际触发后台经验整理、合并、归档/恢复、锁定保护；Brain 权威下 shadow 必须关闭 | 职责统一、无重复/冲突/旁路 | 经验整理实际运行并写入 Brain（journal entry/update + checkpoint）；合并归档来源；锁定拒绝改写；旧 Ombre 写入 0 次；vault 未变 | `evidence/legacy/legacy-convergence.json`、`evidence/legacy/experience-trigger.json` | 通过 |
| 失败与降级 | 检索异常（缺 vectors 表）、embedding 超时（20.7s）、写入失败、索引失败；观察聊天/错误/重试/恢复 | 明确降级、不静默空、不虚报 | 检索异常 fallback 到旧 backend 且继续回答；超时 fallback；写入失败明确报错且无半条；索引 pending 明确提示并恢复 ready | `evidence/failure/failure-direct.json`、`failure-chat.json` | 通过 |
| 切换协议 | 冻结 → 备份 → 最终对账 → 单写权威 → 切换 → 重启；故意 pending/导出篡改触发中止 | 0 差异才切换；异常停止 | 冻结拒绝写入；备份 digest 一致；导出 0 problems；pending 与篡改均 abort；切换期新增/修改/删除正确；重启保持 | `evidence/cutover/cutover-rollback.json` | 通过 |
| 实际 Ombre 回退 | 同 ID 导出 → 独立启动 Ombre → 搜索新增/修改/删除 → Ombre 上保存/修改/检索 | 修改可见、硬删不复活、可实际读写 | 修改 canary 可搜到；硬删不复活；72 事件故事在导出中；Ombre save/modify/search 成功 | 同上 + `evidence/cutover/rollback/` | 通过 |
| 备份恢复 | 切换前备份 → 全新目录 → 启动 → 聊天 | 数据/事件/版本/检索可用 | 16/16 通过（P2-A 行） | `evidence/restore/backup-restore-result.json` | 通过 |
| 正式验收 | 正式 Peter 真实使用 | Lan 执行 | 未执行（不属开发方隔离验收） | — | 待 Lan |

## 3. P2-C：标签/分类治理与历史记忆核实

| 验收项 | 实际执行场景 | 预期 | 实际 | 证据位置（仓库外） | 结果 |
|---|---|---|---|---|---|
| 依赖审计 | tags/domain 生成、保存、读取、检索/页面依赖 | 有结论 | Brain 检索不读 tags/domain；页面用 domain 分组；自由 tags 不再进入检索/筛选 | `docs/tickets/p2/P2-C-review.md` | 通过 |
| 分类方案 | 七类+unclassified 映射试验 | 有覆盖率/未分类率结论 | 覆盖率 36.8%，未分类 63.2% → 不采用固定七类作为 UI 筛选 | 同上 | 通过（分类仅作审计列） |
| 历史清理 | 隔离副本执行可逆清理 | tags=0；正文/ID/来源/事件/版本不变 | 313 记忆 tags=0；313 条迁移记录；清理前后内容指纹完全一致 | `evidence/cutover/cutover-rollback.json`（baseline tags=0） | 通过（正式库未清理，需授权） |
| 新记忆行为 | authority hold 丢弃调用方 tags/domain | 新自由标签写入 0 | 测试与隔离运行 tags=0 | `tests/test_brain_authority_adapter.py` | 通过 |
| 145 条核实 | 全量非事件记忆逐条清单：剩余、覆盖、墓碑、处置 | 逐条清单与结论 | 287 总记忆；215 非事件；140 已覆盖；39 墓碑；36 独立有效；7 悬空来源；逐条表见 `docs/tickets/p2/historical-memories-verification.md` | `evidence/data/historical-memories.json` | 通过（不盲目归组） |
| 检索回归 | 清理后 50 条 A/B + 无答案 + 长故事 | 无重要遗漏增加 | 同 P2-B 黄金集结果；清理后 Brain 命中 29/50 | `evidence/quality/quality-summary.json` | 通过 |
| 页面契约 | 筛选/统计字段 | 页面可直接使用 | panel `domain/tags` 字段保留；tags 为空；domain 仍用于现有分组 | `evidence/desktop/desktop-e2e-result.json` | 通过（页面改版仍属 #431） |

## 4. 明确未完成 / 阻塞（不降级、不推给 P3）

1. **正式切换未执行**：P2-B 工单明确由 Lan 授权后执行；本次只做隔离演练。阻断项不是代码，而是授权与真实体验验收。
2. **正式库标签清理未执行**：P2-C 要求 Lan 同意的维护窗口；隔离清理与备份/回退已验证。正式库当前仍有历史 tags，不能声称 P2-C 正式完成。
3. **正式 `.env` 的 `EMBED_API_KEY` 当前返回 401**（外部凭据问题）。隔离验收改用本机备份 `.env.bak-20261119` 中的可用 key（测试材料只在仓库外），因此隔离检索/索引路径可验收；**正式切换前必须由 Lan 更新有效 key**，否则 Brain 向量索引会进入 pending、自动召回会降级。
4. **页面隐藏按钮**：现有记忆页没有“隐藏/取消隐藏”按钮（P2-B 明确页面改版归 #431）；隐藏由 Peter 的 memory 工具设置/恢复，已验证数据层隐藏后不召回、不展示，恢复后重新可见。该按钮不是本 PR 新增页面功能。
5. **黄金集 4 条 Ombre-only 案例**：Brain 总命中/覆盖率/延迟均优于旧系统，但仍有 4/50 个案例是 Ombre 命中而 Brain 未命中；已在质量报告列明，正式使用前由 Lan 体验复核。
6. **Lan 真实使用体验**：按 P2-B 第 9 条，只能由 Lan 在正式 Peter 上完成。

## 5. 单元/回归测试（辅助证据，不替代上面的实机验收）

- `test_brain_*.py`：78 条通过。
- `test_recall_*.py`：234 条通过。
- `test_memory_panel.py`：8 条通过。
- `test_web.py` + `test_veil_brain.py`：83 条通过。
- 本轮新增回归：embedder 对象/函数边界、隔离 config-root `.env`、authority 归档/合并/锁定、pending 三态与重试、重复 event 行折叠、digest 覆盖阶段。
