# 工单 461 · 测试与隔离运行证据

> 基线 `f979ca2`（PR #460 已合并）→ 合流 PR #461 后 `504c0a7` · worktree `worktrees/461-p5-s6-gates` · 2026-11-27 +08:00
> 所有测试使用临时目录或 VEIL Lab 合成数据，未写正式 `data/peter`，未启停正式 Peter。

## 1. 定向回归（全通过）

| 命令 | 结果 |
| --- | --- |
| `python -m unittest tests.test_p5_health_period_orphans -v` | 7/7 通过：孤立/悬空区分、768/1024/1536 动态维度、模型身份不一致、期间增长/重复/碎片/断链/索引、快照历史、无范围时 `not_requested`、严格只读 |
| `python -m unittest tests.test_p6_s6_chat_evidence_budget -v` | 11/11 通过：增量解析只处理新行、400 条 LRU + 按需旧证据、同长度改写不命中旧缓存、删除日志即失效、重复/召回不算证据、旧 200 行窗口外仍能核验、截断→unavailable、共享预算、真实 SQLite 排他锁 |
| `python -m unittest tests.test_p6_s6_idempotent_writes -v` | 7/7 通过：facts 追加/修订、relationship 追加、ongoing 新增的重复提交、丢响应后重启重试、4 线程并发只写一次、显式键参数冲突拒绝 |
| `python -m unittest tests.test_p6_s6_cross_language_veto -v` | 4/4 通过：无共同锚点翻译候选必须经语义核验、无关正例晋升、Lan 显式恢复旧认知、code fence 严格 JSON |
| `python -m unittest tests.test_learned_context tests.test_pr460_evidence tests.test_relationship_semantic_state tests.test_relationship_semantic_worker -v` | 通过（S3 证据链、语义回执、预算、重启/并发不变量） |
| `python -m unittest tests.test_relationship_422 tests.test_p6_s3_relationship_governance tests.test_pr460_sovereignty -v` | 通过（实际请求注入、四区预算、用户主权、否决防复活） |
| `node --test desktop/tests/health-period.test.cjs desktop/tests/ui.test.cjs desktop/tests/workspace-budget.test.cjs` | 通过（健康页期间卡片、既有桌面 UI 回归） |

## 1.1 合流 PR #461（S5）后的联合回归

PR #461（工单 460 / P6-S5）于 2026-11-27 12:59 UTC 合并为 `504c0a7`。本单分支已合流该提交（共享的 `desktop/workspace-brain.js`、`veil/brain/brain_p4_api.py`、三份状态文档按区域合并，S5 检索/判官/联想逻辑未改）。

- 联合定向回归（126 项）：`tests.test_p5_health_period_orphans` + 461 三个 S6 测试 + `test_relationship_422` + `test_p6_s3_relationship_governance` + `test_pr460_sovereignty` + `test_relationship_semantic_worker` + `test_p6_s5_retrieval_judge_association` 全部通过。
- 桌面 Node：`node --test desktop/tests/*.test.cjs`，58/58 通过（S5 双开关 + 本单健康期间卡片同文件）。
- 合流后完整 Python 套件：2469 项，18 失败 + 3 错误 + 29 跳过，失败集合与下文基线逐项相同。

## 2. 完整 Python 套件

命令（与 CI 一致）：

```
python -m unittest discover -s tests
```

本单 worktree 结果：合流前 **2457 项，18 失败 + 3 错误 + 29 跳过**，耗时约 423s；合流 PR #461 后 **2469 项，18 失败 + 3 错误 + 29 跳过**，失败集合相同。

在 `origin/main` `f979ca2` 的独立基线 worktree 重跑同一批失败模块：

结果：**18 失败 + 3 错误**，失败/错误名称逐项相同：

- `tests.test_maintenance` 17 项（CRLF/暂存区、工作台、接受与合并门禁等既有环境问题）
- `tests.test_backup_restore / test_reliability_closeout` 2 项（本机存在 APPDATA 桌面配置时 `desktop-data` 组件加入清单；空 APPDATA 路径此前已通过）
- `tests.test_executor_opencode` 1 项（未安装 OpenCode，缺少输出文件）
- `tests.test_create_manifest_covers_local_state_and_omits_only_runtime_files` 等

结论：本单未新增失败；失败集合与基线完全一致。HANDOFF 在 PR #460 时已记录这些既有失败，本单未修改相关模块。

## 3. 性能与预算实测

| 场景 | 数据 | 结果 |
| --- | --- | --- |
| 聊天索引首次建立 | 50,000 条用户消息 / 6,677,835 B | 0.256s |
| 连续无变化调用 | 同上 | 0.435ms/次（只 stat + 5 个 64B 指纹） |
| 追加 1 条后重建 | 同上 | 0.824ms，仅解析新行 |
| 旧证据按需查询 | u0 / u25000 | 1.03ms / 10.64ms；命中 LRU 0.52ms |
| 旧实现全量读取+解析（对照） | 同上 | 73.7ms/次（不含旧实现的全量 SHA-256） |
| 真实 SQLite 排他锁 + 4 条关系认知 + 300ms 预算 | 临时 brain.db | `build_prompt` + `relationship_items` < 3s 测试上限；全部未晋升，预算耗尽项 `budget_exhausted` |

完整数字见 `docs/reports/461-chat-index-benchmark-20261127.json`。

## 4. 真实模型实验（隔离、受预算）

见 `docs/reports/461-cross-language-live-20261127.md` 与 `veil-lab/runs/461-sem-synthetic-20261127T210905/reports/latest.md`：

- DeepSeek `deepseek-flash`，2 次真实调用，本 run ¥0.00236848；本单累计语义探针 ¥0.0509（上限 ¥2）。该记录只属于这次手工实验；CI 与 `tests/test_p6_s6_cross_language_veto.py` 使用假模型，不调用 `api.deepseek.com`。
- 无共同文字锚点的翻译式否决：`contradicted`，不注入。
- 同一否决在场的无关主题正例：`supported`，晋升并注入。
- Lan 显式恢复旧认知：确定性单测覆盖，不依赖模型。

## 5. 未覆盖 / 边界

- 未在正式 Peter 上运行，未部署。
- 主聊天模型自行提取候选的完整链路未在真实聊天中复测；实验候选由脚本提交到生产入口（与 PR #460 相同边界）。
- 聊天索引首次建立仍读取完整历史；同长度原地改写若只发生在 5 个采样窗口之外且 mtime/size 不变，理论上仍可能命中旧缓存（详见工单边界）。
- 反证超过 120 条时按相关性分批，不因无关积累停止其他晋升；相关否决不丢。
