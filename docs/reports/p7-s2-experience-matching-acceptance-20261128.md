# P7-S2 情境识别、语义匹配与经验注入施工验收报告

> 工单 471 · 基线 `73d93e7` · 分支 `ticket/471-p7-s2-hybrid-matching` · 2026-11-28 +08:00

## 结论

P7-S2 同秒候选向量缓存并发覆盖阻断已解决。沿用现有 S2 实现，仅将缓存新旧判断改为跨实例写锁内分配的候选级单调 `generation`；固定相同时间条件下 model-A → model-B → model-A 旧快照保存的回归先红后绿，Provider 保留 model-B 语义命中且不新增 Embedding。S2/S1 缓存相关专项 29/29、原定向集加该回归 182/182 通过，可提交 PR 等待复审。

本单未启停正式 Peter，未读取或写入正式 `data/peter`，未部署，未调用真实模型或 Embedding，费用 ¥0。开发侧回归通过不代替 CI、PR 复审或 Lan 体验验收。

## 交付范围

| 规划门槛 | 实际交付 | 证据 |
| --- | --- | --- |
| Stage 1 混合匹配 | `0.55*语义 + 0.25*信号 + 0.20*词法`，有向量要求总分 `>=0.42` 且语义 `>=0.36`；无向量严格回退两词项+域门控 | `test_zero_lexical_cross_language_and_target_after_200_are_scored`、`test_short_ack_and_unrelated_scene_are_zero_injection` |
| 候选懒向量与同轮复用 | P7 关闭不查询；显式开启时按 `trigger | domain | type` 指纹、模型、维度预热一次；每轮只读 `SituationEnvelope.query_vector` 与缓存候选向量 | `test_disabled_costs_zero_and_enable_warms_once` |
| `P7-MATCH-02` | `semantic_pool=True` 返回完整合格候选集，Provider 全部评分后再 Top-4，不再先取最新 200 条 | 205 个干扰候选 + 旧目标隔离测试 |
| 例外、阈值、冷却 | 短应和入口前短路；例外强命中关门；同会话默认 4 轮冷却，新错误码可重触发 | `test_entry_domains_exception_head_and_cooldown`、`test_exception_and_conflict_priority_are_deterministic` |
| 冲突与协同去重 | 显式冲突/版本链按 Lan 确认、证据等级、验证时间、匹配分稳定裁决；HEAD 命中跳过；普通召回复述降权 | 同上 + Chat 完整回归 |
| 多入口 | Chat/mobile/voice 全域；工具补检仅错误、扩组、no-progress 且只允许 `work_method`；自主工作态与闲暇态分域；glance/codex_watch 跳过 | `test_entry_domains_exception_head_and_cooldown` + `tests.test_chat` |
| 可选灰区复核 | 默认关闭；只送最多 3 条 `[0.34,0.48)` 候选；复用背景模型，2 秒超时，`p7_experience_gate` 独立日预算，异常与超额 fail-closed | `test_gray_gate_default_off_and_independent_cap` |
| 既有能力不回归 | 保持 S1 证据/否决/角色隔离/备份，P7 仍在 AUXILIARY，普通记忆与缓存前缀结构不变 | S1 20 项 + Chat 117 项 + 定向回归 |

## 影响与接入核对

| 用户场景 | 代码证据 | 改后行为 | 验证 |
| --- | --- | --- | --- |
| 日常聊天，P7 关闭 | `ExperienceHintProvider.__call__` | 候选查询、候选 Embed、注入均为 0 | S2 专项 |
| 换措辞/跨语言/隐含情境 | Provider 混合评分 | 零词项仍可由同轮向量与情境信号命中 | S2 专项 |
| 无关闲聊/短应和/例外 | 短路与 Door 例外门控 | `no_match`，0 Token | S2 专项 + Chat |
| 候选超过 200 条 | `query_experience_candidates(semantic_pool=True)` | 语义评分前不截断，旧相关项不被淘汰 | 206 条隔离造数 |
| 多步工具 | `_tool_chat` 批次结果后补检 | 仅错误/扩组/no-progress，首轮+补检仍共用 4 条/600 tokens | Chat 回归 |
| 自主活动 | `wake_context` + Provider 域门控 | 工作态只工作/承诺，闲暇态只自主实践 | S2 专项；自主完整模块受既有测试导入问题限制 |
| 权限与费用 | 既有 AUXILIARY + Budget | 不改变工具权限；灰区复核独立硬顶且默认关闭 | 结构检查 + 专项测试 |
| 停止/恢复、数据、升级 | 无新增任务；向量随候选状态同文件备份 | 关闭即停止；旧记录无向量可词法降级；配置/状态沿用 S1 | S1 回归 |

八项接入结论：入口覆盖 Chat、工具步骤、自主与显式跳过模式；无新增后台任务，关闭即停；权限体系不变；标准档每轮 0 新增模型/Embedding 调用，可选灰区独立预算；候选向量仍存角色隔离巩固状态并纳入 S1 协同备份；诊断沿用请求局部 P7 状态；旧记录零迁移；正式 Windows 环境未部署，留待后续阶段。

## 新鲜验证

- 同秒回归：`python -m unittest tests.test_p7_s2_experience_matching.P7S2ExperienceMatchingTests.test_same_second_model_switch_survives_interleaved_stale_saves -v`：修复前稳定失败（实际 model-A，预期 model-B），修复后 1/1 通过。
- 缓存相关专项：`python -m unittest tests.test_p7_s2_experience_matching tests.test_p7_s1_experience_provider -q`：29/29 通过。
- 最终定向回归仅运行一次：`python -m unittest tests.test_p7_s2_experience_matching tests.test_p7_s1_experience_provider tests.test_chat tests.test_p6_s6_3_cognitive_consolidation tests.test_autonomy_cleanup tests.test_config_registry tests.test_brain_backup -q`：182/182 通过（35.372s，exit 0；原 181 项 + 新增同秒回归）。
- 含 `tests.test_autonomy` 的 230 项组合：226 项通过，4 项在进入本单代码前因测试内顶层 `from test_tools import ToolLoopTests` 找不到模块而报错；补 `PYTHONPATH` 重跑仍为同一 4 项。按“同一问题两次未解决即停止”纪律未做第三次修改或重跑；本单未修改该导入或测试工具模块。
- `python -m py_compile`：修改的 Python 文件与新测试通过。
- 完整仓库级回归交由 GitHub CI；未运行真实模型、正式数据、正式桌面部署或付费实验。

## 已解决阻断

- **根因**：旧快照合并依赖秒级 `cached_at`，相同时间无法区分两次模型向量提交。此前测试未固定相同时间条件。
- **修复**：复用 `mutex + brain-write.lock`，`cache_experience_embedding()` 在锁内重读当前候选并分配 `generation + 1`。`_merge_candidate_embedding()` 按 generation 合并，同代保留磁盘已提交值；旧记录缺失或无效 generation 按 0 处理。`cached_at` 保留作诊断，不参与并发正确性判断。
- **影响路径**：显式启用/确认预热 → 缓存写入 → 后台旧快照 `_save_state()` → Provider 模型/维度校验；仅增加候选缓存字段，不修改匹配、治理或预算接口。两个独立 Consolidator 固定同秒交错保存，断言 model-B/dim-2/generation-2 保留，零词项中文情境仍语义命中且无额外 Embed。
- **关闭证据**：新增回归修复前稳定读回 model-A 而失败，修复后通过；缓存相关 29/29、最终定向 182/182 通过。未展开新架构、S3～S5 或无关测试导入修复。

## 明确未施工

- 未施工 S3 的四级晋升、反馈账本、`experience_reflect` 与行为结果闭环。
- 未施工 S4 的 Brain UI/H-8/费用页细化，也未施工 S5 的 10 场景 ON/OFF 回复回放。
- 候选向量在显式开启时预热；若 Embedder 不可用则保持无向量并走严格词法+域门控，不在聊天轮次补调用。
