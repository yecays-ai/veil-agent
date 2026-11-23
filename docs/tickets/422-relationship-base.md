# 工单 422 · 稳定关系底色与共同经历自然联想

## 目标（Lan 2026-11-23）

保存下来的共同生活要参与日常聊天，而不是只在被问「你还记得吗」时才出现：

1. 每轮都带一份很小但有效的关系底色；相关召回为 0 也不失忆。
2. 共同经历可以由话题关联或个人意义自然浮现，但不纯随机、不固定频率。
3. 一次联想可以承接连续几轮，换话题后释放。
4. 记忆影响措辞、理解和行为，不强迫 Peter 复述往事。
5. 诊断分开显示关系底色、相关召回、自然联想和复用。
6. 不新增第二套记忆系统，不恢复三条／600 字限制，不增加未经批准的每轮付费调用。

## 现状（本 PR 实现）

### 1. 每轮最小关系底色

- 复用 `learned_context`（同一个 `learned-context.json`），新增可选 `relationship` 段：每条是
  「现在还成立的一件事」，带 `status`（`confirmed` / `uncertain` / `outdated`）和 `source`
  （`lan` / `evidence` / `peter`）。旧文件没有这个段时按空处理，不改写文件。
- 新增 `relationship_base.py`：按 `confirmed` → `uncertain` 排序（Lan 的话 > 反复证据 > Peter 自己的
  理解，同类看更新时间），只取整条、永不截半句，默认上限 480 字 / 8 条。`outdated` 只留在文件里
  当历史，不进请求；`uncertain` 带 `(not confirmed yet)` 标注，不冒充共识。
- 与 `facts`／`growth` 完全重复的条目不再重复注入。
- 作为独立 system 块（标签 `relationship_base`）放在请求稳定前缀里，因此计入「必需块」，
  不会被记忆预算挤掉；它没有内容时（没有当前条目）就不出现，也不花 token。
- 预算不足时保留能放下的条目，并在 `relationship_base` 诊断里记 `degraded` 和原因（`space` /
  `item_cap` / `space_and_cap`）以及被舍弃的条目。不会静默整块消失。

### 2. 共同经历自然联想

- 三条路径分开：
  - 明确回忆：沿用现有相关召回（`asks_about_past`）。
  - 话题相关联想：沿用现有检索 + 判官（`relevant_recall`）。
  - 弱自然浮现：只用 Ombre 自己的「忽然想起来（非检索命中）」候选，新增
    `natural_association.py` 做**确定性打分**——存储重要度（pinned 视为 9）、距上次浮现的轮数、
    冷却、这条是否已在本次请求里、以及「这句话是不是轻松闲聊」。**不再用 `random_chance` 摇概率**，
    也没有「每 N 轮」规则；没有合适的就是 0 条。
- 轻松闲聊门槛：超过 `association_light_chars`（默认 40 字）或带工作／报错／日志／部署等词的句子
  不会联想，低重要度的技术琐事也不会闯进吃饭闲聊。
- 承接：沿用第二步／#419 已有的追问承接（`is_followup` + `last_injected_refs`）。上一轮出现的联想在
  「后来呢／那我呢」这类追问里以 `followup` 复用（诊断记为 `reused`），不再重新检索；明显换话题时
  追问判定不成立，联想随之释放，回来按正常检索重新判断。没有新的会话状态机。
- 联想不要求说出口：#422 只决定它是否进入视野，措辞由现有人设和回复模型决定。

### 3. 维护与用户控制

- Peter 用现有 `profile_append` / `profile_replace` 的 `section: "relationship"` 维护：
  新增可带 `status`；`profile_replace` 把旧条目标成 `outdated` 并写 `superseded_by`，于是
  「以前以为 X，后来确认 Y」留在文件里可读，当前请求只带 Y。`new_text` 传空即作废该条。
- Lan 在「Peter · 他记的你」里新增「当前关系」一块：改文字、改状态（已确认／还没确认／不再适用）、
  新增、两次点击删除；保存走 `save_relationship`，`source` 记为 `lan`；每次改动写进
  `learned-context.versions.jsonl`，可回看。
- 隐藏、锁定、归档按现有记忆机制处理：联想只从 `context_visible`（已过滤隐藏／归档／角色隔离）之后
  的候选里选，被隐藏的记忆不会通过联想重新出现。

### 4. 预算、延迟和降级

- 沿用 #419 的整轮记忆预算：关系底色属于必需块，相关事件与弱联想想用剩余记忆预算，完整故事优先；
  联想条目排在相关事件之后，预算不足时先舍弃联想。
- 不恢复固定三条上限，不做 600 字截断，不每轮加载完整档案。
- 不新增任何模型调用：弱联想只是对已经取回的候选做算术。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `learned_context.py` | `relationship` 段（读取/校验/字节统计/保存/工具写入/版本记录） |
| `learned_tools.py` | `profile_append` / `profile_replace` 的 `section` 增加 `relationship`，新增 `status` |
| `relationship_base.py` | 新增：稳定关系底色的选择与渲染、自身小预算、降级原因 |
| `natural_association.py` | 新增：弱自然联想的确定性打分与门槛 |
| `memory_agent.py` | 用打分替换随机抽取；诊断新增 `relationship_base` / `relevant_recall` / `natural_association` / `association` |
| `recall_rotation.py` | `[memory.recall]` 新增联想配置项；`random_chance` 只作兼容开关 |
| `veil_agent.py` | 请求组装里注入 `relationship_base` 块并记录诊断 |
| `web_agent.py` | 上下文页透传新诊断；Peter 页 `save_relationship` |
| `prompts/registry.py`, `prompts/defaults/memory.relationship_base.header.md`, `prompts/defaults/memory.association.label.md` | 两段新文字登记（可在「指令」页改） |
| `desktop/chat.js`, `desktop/chat.html`, `desktop/peter-page.js` | 诊断显示四个分类；Peter 页「当前关系」卡片 |
| `characters/peter/character.toml` | 新配置项注释（默认值不变） |
| `tests/test_relationship_422.py` | 新验收测试 26 项（含复审四项） |
| `tests/test_recall_judge.py` | 判官与联想互不否决、不重复注入的两条回归 |

## 首次复审返工（2026-11-23）

Lan 的审核意见指出四个根因，本 PR 已按项修复（未重写整体）：

1. **判官会清空自然联想候选**：删掉「判官成功即 `drift = []`」。判官只回答「哪些旧事和这句话直接
   相关」，不再顺便否决弱自然浮现；两条路径共用可见性、冷却、事件去重和总预算，判官已选中的条目
   不会作为联想重复进入。`test_recall_judge.py` 的历史断言改为区分旧随机路径与受控联想。
2. **候选上游只有 Ombre 的随机浮现**：候选池改为 Ombre 自己的重要度序列（`breath()`，会话内已缓存，
   不新增调用、不扫全库）加随机浮现（低优先、固定扣分）。高重要且久未浮现的条目能参与；随机低意义
   条目不能因为恰好是唯一漂移就获胜；隐藏／归档仍被过滤；同库同轮结果稳定。
3. **页面保存把所有来源改成 Lan**：`save_relationship()` 按稳定 id 与保存前逐条比较，只有 Lan 新增、
   改正文或改状态的条目才记 `source=lan`；未变条目保留原来源、创建时间、更新时间与证据。
4. **`evidence` 没有写入路径**：关系条目新增最小 `sources` 证据引用（`chat:`/`memory:`/`event:`/
   `long_task:` + 编号，程序校验形状）。Peter 写 `confirmed` 时必须有可校验来源：`chat:<id>` 必须
   真的出现在本角色聊天记录里，否则自动降级为 `uncertain`/`peter` 并在工具结果里说明原因；有可查
   来源时记为 `evidence`。页面保存的条目来源是 `lan`，并保留证据。`random_chance` 不再被读取（不是
   隐藏开关）；要关掉联想只有 `association_enabled = false`。

返工新增回归：`tests/test_recall_judge.py` 两条（判官空选择后联想仍可进入；判官选中的条目不会重复），
`tests/test_relationship_422.py` 从 15 条增到 26 条（判官路径、候选池来源与稳定性、页面逐条来源、
证据降级/伪造来源拒绝、改正后的历史与事件故事不被覆盖、经真实工具循环写入的条目带消息来源）。

## 验收（Lan 的 Windows 本体体验）

1. 更新并重启后，打开一页「上下文」，随便聊一句和旧事无关的话（例如「今天下班有点累」）：
   这一轮应显示「关系底色 N 条」，而「相关 0 条」不再等于他没有关系记忆。
2. 打开「Peter · 他记的你 → 当前关系」，新增一条「已确认」和一条「还没确认」，保存；
   下一轮那句普通聊天带的就是这两条，其中「还没确认」标着是他自己的猜测。
3. 把一条改成「不再适用」再保存：下一轮不再出现，但它还留在文件／版本记录里。
4. 聊一件和旧事有真实联系的事（不说「还记得吗」），看他能不能自然接上完整经过；
   再聊一件完全无关的轻松小事，允许他这轮一条旧事都不提。
5. 让一条联想出现后连续追问两轮（「后来呢」「那我呢」），看上下文页的「复用」；再明显换话题，
   确认它不再跟着；回到原话题时按正常检索重新判断。
6. 观察几天：不应出现「每几轮必翻旧账」，也不应在你说工作报错时突然跳到无关旧事。

## 未做 / 未验证

- 没有新增固定付费模型调用，也没有做真实模型抽查（本单只有隔离自动化；真实回复体验要 Lan 按上面
  六步验收，费用为 0 次额外调用）。
- `memory:`/`event:` 证据引用只校验形状、保留为指针，本单没有反查事件库；反查留给第五步迁移一起看。
- 正式 68 篇迁移、QQ 适配、手机端连接不在本单。
- 旧 `about-lan.md` 迁移逻辑未改；`relationship` 段一开始是空的，Peter 或 Lan 写第一条后才出现。
