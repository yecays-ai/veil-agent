# HANDOFF

> 更新：2026-11-28 21:08 +08:00 · 基线 `main = 73d93e7` · 分支 `ticket/471-p7-s2-hybrid-matching` · 工单 471 · PR #472 首次红 CI 已修复，`c44e836` 全绿；复审新增普通召回重复注入已补回归，本地 230/230，新 CI 待运行；issue #471 已按 Lan 指令关闭
>
> 部署状态、阶段结论与验收入口只以 [STATUS.md](STATUS.md) 为权威来源，本文件不重复记录。

## 当前停点

- HEAD 语义去重已补全真实 Chat 接线，显式 enable/confirm 预热 HEAD/guidance 并持久化，普通轮次只读 cosine>=0.85。跨语言 HEAD/无 HEAD 的完整 Chat.send 对照已先红后绿，模型/端点/文本失效、重建复用与角色备份恢复均有回归；修复渲染计数被过滤原因列表覆盖及候选查询平方级扫描。最终定向 **238/238**；本地不跑全仓。旧的“HEAD 未实现”停点已失效，后续以最新 PR 提交的 CI 为准。

- P7-S2 的混合匹配、候选向量启用/确认时预热、`P7-MATCH-02`、短应和/阈值/例外/冷却、冲突与 HEAD/普通召回去重、工具步骤补检、自主分域、灰区复核独立预算均已实现。
- 本窗口按 Lan 明确指令仅修复同秒缓存并发覆盖：候选向量 `generation` 在角色跨进程写锁内读取磁盘值并递增；快照合并按 generation 选新值，同代保留磁盘值，旧格式按 0 处理，`cached_at` 仅用于诊断。
- 固定同秒、两个独立 Consolidator 的 model-A → model-B → A 旧快照保存回归先红后绿，Provider 继续使用 model-B 向量且无新增 Embed。
- 工单 471 · PR #472 的首轮 CI 在四个矩阵中统一失败：新增灰区 system prompt 未注册；已迁入 `prompts/defaults` 并在 registry 登记，`tests.test_prompt_registry` 32/32。
- Sourcery 实质问题已集中处理：非有限/损坏向量、工具阈值、词法弱回退、并发冷却及预热、缓存向量空间、短应和标点、旧格式归一化、工具错误摘要、非用户轮次计数；真实 206 候选和 500 候选惰性冲突回归通过。预算跨进程意见经 CLI/Web 数据根排他锁核实为当前支持入口不可达的条件性风险；初始提示上限计数误报（已有 `p7_debug` 传递）；同 Chat 向量混用在模型锁内不可复现，均需在 PR 解释。
- `c44e836` 四平台 Python 3.11/3.12 的 Tests 与依赖安全 CI 全绿；Sourcery 对该提交新增一条普通召回重复注入问题，已用先红后绿回归改为命中已召回 guidance 时直接跳过，不再仅减 0.15 分。更新后本地定向 230/230（38.906 秒，exit 0）；提交后新 CI 待验证。
- 正式 Peter、正式数据、正式配置与正式运行实例均未改动；未调用真实模型/Embedding，费用 ¥0。

## 下一步

1. 核对 PR #472 最新提交的 GitHub CI、Sourcery 复审结论；按 Lan 要求关联并关闭 issue #471，但 PR 未合并。
2. 如新 CI 有真实失败，先取证再集中处理；切勿将旧提交的红灯写成最新结果。
3. 不合并、不部署、不启动 P7-S3～S5。

## 独占文件

- `veil/brain/experience_provider.py`
- `veil/brain/cognitive_consolidation.py`
- `veil/core/veil_agent.py`
- `tests/test_p7_s2_experience_matching.py`
- `tests/test_chat.py`
- 工单、状态、交接、验收与施工报告文档

## 尚未施工

- P7-S3：四级证据晋升、自主判断工具、反馈账本与行为结果闭环。
- P7-S4：Brain UI、H-8 与费用/诊断页面细化。
- P7-S5：VEIL Lab 10 场景 ON/OFF 回复回放与上线门禁。
- 正式 Windows 部署、正式灰度开启与真实日用模型效果验收。
