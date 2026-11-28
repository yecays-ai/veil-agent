# 工单 471 · P7-S2 情境识别、语义匹配与经验注入

- **产品格子**：A 陪伴核心。
- **做完后 Lan 能多做的一件事**：Peter 在换语言、换说法或只隐含表达同类情境时，仍能准确想起已确认经验，并在无关聊天、短应和与不适用例外中保持安静。
- **Lan 怎么验收**：使用隔离假数据即时验证零词项中文改写、双向跨语言、隐含情境、200 条以上候选、无关场景零注入、工具步骤与自主唤醒分域；P7 的统一回复体验仍在 S5 验收。

## 目标

严格按 P7-0 正式规划 §7.2 完成 P7-S2：在 S1 生产 Provider 和 `SituationEnvelope` 上落地本地混合匹配、候选向量懒缓存、阈值/例外/冷却、冲突与协同去重、多入口补检、可选灰区复核，并关闭 `P7-MATCH-02`；不提前施工 S3～S5。

## 当前状态

- 基线：`main = 73d93e7`（工单 469 · PR #470 已合并，合并提交 `73d93e7`；GitHub Actions 全绿；未部署；Lan 即时体验统一留到 P7-S5）。
- 分支 / worktree：`ticket/471-p7-s2-hybrid-matching` / `worktrees/471-p7-s2`。
- 直接复用：S1 `ExperienceHintProvider`、证据新鲜度与否决复核、角色配置、同轮 `Memory._turn_query_vector`、S5 `context_signal`、关系/成长 HEAD、普通召回、`_wake_situation()`、工具循环与共享 `Budget`；不建立平行记忆、检索、权限或费用系统。
- 复用规划调研：沿用 P7-0 对 Nocturne Conditional Disclosure 的结论，只借 Door 条件门控思路，不新增依赖。
- 独占文件：`veil/brain/experience_provider.py`、`veil/brain/cognitive_consolidation.py`、`veil/core/veil_agent.py`、`tests/test_p7_s2_experience_matching.py` 及本单状态/验收文档。

## 验收步骤

1. 在隔离数据中用零词项重合的中文改写、中文/英文双向改写和隐含工作/关系/承诺情境，确认正确经验命中；短应和、无关闲聊、命中例外均为 0 Token。
2. 构造 200 条以上 confirmed 候选，把唯一相关经验放在旧候选区，确认语义评分前不被截断；关闭 P7 时候选查询与候选向量调用均为 0，开启时只在显式开启动作预热一次，每轮不新增 Embedding。
3. 构造冲突、HEAD 已覆盖、同会话重复、工具错误/扩组/no-progress、自主工作态/闲暇态与灰区预算场景，确认主权裁决、去重、冷却、域隔离和独立费用上限均生效。
