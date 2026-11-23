# 工单 432 · 开发者诊断：全仓异常来源清单 + 只读诊断页

- 归属：**维护类，不占 A–V 五条线的格子**（Lan 2026-11-23 确认）
- 基线：`main` = `b1e51f1`（工单 429 合并后）
- 分支：`ticket/432-dev-diagnostics`；开发用独立 clone（放在 VEIL 根目录旁边，不写正式目录的 `.git`）
  （施工沙箱里正式目录的 `.git` 不可写，因此用独立 clone，等价隔离；不动正式目录和其他工单的目录）
- 编号说明：431 被空分支 `ticket/431-brain-ui-reuse` 占着（留给记忆组的 Brain UI），本单用 432

## 目标

Lan 出问题时打开一个页面，30 秒内知道"哪儿不对、最近出了什么事、这一轮为什么失败"，并能把一条记录**一键复制**给维护 agent，不用再逐页翻。

上一版审计只覆盖了"已经有账本可读的失败"。Lan 用两个真实例子指出缺口：工具清单里「花园社区」长期超预算没人修；自主活动时模型接口经常挂、权限经常被拒。核实后确认这两类都不会进入"最近异常"。

**所以本单先补异常来源清单，再施工页面。**

## 现状（施工前实测，2026-11-23 23:40，正式库）

| 项 | 实际 | 上限 | 结论 |
|---|---|---|---|
| `garden_more`（花园其他） | **15,911 字节** | 12,000 | 超 3,911（+32.6%） |
| `always_on`（一直带着的工具） | **33,934** | 36,000 | 94.3%，只剩 2,066 |
| 工具总数 | 126 | — | — |

- 计算能力有（`tool_groups.inventory()` 返回 `over`），显示只有一处：上下文页 →「工具清单」列表顶部一行。
- **没有时间、没有历史、不提醒、没有入口。**
- **CI 不会红**：`tests/test_tool_budget.py` 只查①静态单工具 ≤1,500②常驻总量 ≤36,000；组预算只用虚构超大工具证明"能算"。**MCP 工具的定义是运行时才有的，所以花园组的组预算从来没被任何测试覆盖。**

| 自主活动 | 实际 |
|---|---|
| 总次数 / 失败 / 部分完成 / 搁置 / 被打断 | 328 / 31 / 169 / 20 / 13 |
| 31 次失败里模型接口导致 | **26 次**（401×13、400×8、超时×3、503×2） |
| `provider-errors` 总数 / 近 7 天 | 342 / **119** |
| `context-log` 带 `autonomy_run_id` 的行 | **0**（本单补） |

| 权限被拒绝 | 实际 |
|---|---|
| `state=被拒绝` | **607 条**（自主活动 184 条；2026-11-24 00:55 读取，数据还会继续增长） |
| 带原因 / 完全没有原因 | 554 / **51** |
| 原因文案种类 | ≥20 种 |
| `pending-approvals.json` | **不存在** → 那 184 条不是"等你批准" |

其它空白：`memory/_agent/diagnostics.jsonl` 472 行**没有任何读取方**；MCP 错误只活在内存；服务崩溃只写一行覆盖式文本（traceback 丢进 DEVNULL）；桌面断线不落盘；81 个根模块共 1,311 个 `except` 分支，绝大多数是降级/清理，不产生记录——**"没记录"不等于"没出错"**。

## 异常来源清单

页面按 12 个模块与 7 类异常来源展示，每行回答：是否产生记录 / 是否持久 / 页面能否读到 / 能否关联一轮 / 能否判断原因 / 判断不了显示什么。**没有记录就显示「未知」，不显示「正常」。**

必须验收的三类：

1. **工具组超预算** →「配置校验」直接给出 15,911 / 12,000 与超出量，可复制。
2. **自主活动 API 失败** → 一条异常带 `run_id` / `request_id`，点开能看到这次唤醒的模型请求、工具与结果。
3. **权限拒绝分类** → 6 类标签（等你批准 / 你拒绝 / 规则阻止 / 参数错误 / 安全护栏 / 未分类），每条带分类依据。

## 改法

**新增**

- `diagnostics.py`：只读聚合（模块状态、异常时间线、单轮链路、配置校验、拒绝分类、报告文本、快照）。只用标准库；不 import 时读文件；任何异常降级成「未知」。可命令行独立运行：`python diagnostics.py --data-root data --role peter`（服务已死也能出报告）。
- `desktop/diagnostics-page.js`：页面渲染（IIFE，零依赖）。
- `tests/test_diagnostics.py`：27 条（虚构数据）。

**修改（最小集）**

| 文件 | 改什么 |
|---|---|
| `veil_agent.py` | `_request_failed(exc, request_id)` 增加 `request_id/kind/run_id` 字段；`_log_context` 增加 `autonomy_run_id`（自主活动行） |
| `web_agent.py` | 5 个只读接口；`Hub.data_root` / `Hub.started_at`（2 行） |
| `desktop/chat.html` | 导航项 + 页面骨架 + 脚本引用 |
| `desktop/chat.js` | `setPage` 白名单 + 点击钩子（3 行） |
| `desktop/main.cjs` | GET 白名单 5 条 + `chat:open-maintenance`（只接受入口 id，只开服务端配置的 127.0.0.1/localhost 地址） |
| `desktop/desktop-preload.cjs` | `openMaintenance` |
| `desktop/chat.css` | 诊断页样式（`.diag-*`，不影响其他页） |
| `desktop/tests/ui.test.cjs` | 导航断言更新 + 诊断页守卫 |

**接口**

```
GET /api/diagnostics/overview                     版本/系统/模块/配置/异常/计数
GET /api/diagnostics/issues?hours=&module=&limit= 统一时间线（含分类与兜底文案）
GET /api/diagnostics/trace?request_id=|run_id=|call_id=
GET /api/diagnostics/report?scope=overview|issue&id=  返回 {text}（脱敏唯一实现在服务端）
GET /api/diagnostics/links                        维护入口（记忆组给 URL 就自动出现）
```

**关联规则（Lan 明确要求）**

- 只认结构字段：`request_id` / `call_id` / `autonomy_run_id`。
- 自主活动的 run id 就是 `autonomy.jsonl` 的 `started_at`（代码里 `self.chat.autonomy_run_id = started_at`），不需要再加第三个字段。
- **拿不到 id 的旧记录一律标「可能关联，未经确认」，绝不只凭时间推断归属。**
- 恢复状态只有"同一 id 之后确实成功"才写「已恢复」，否则「恢复未知」。

**拒绝分类的证据规则**

- `pending-approvals.json` 里 run_id 相同 → 精确（exact），分别给出 waiting / expired / denied。
- 其余按文案规则（replay 护栏、参数错误、路径/权限、状态冲突、次数限制、功能不可用），每条都带"文案：xxx"作为依据。
- **不为凑指标强行归类**：实测 607 条拒绝中 20.1%（122 条）仍是「未分类」，分三档可解释：51 条没有原因文本、31 条详情被活动摘要覆盖（旧记录）、40 条没有匹配规则。页面与报告必须给出这三档数字。

## 边界（写死）

不新增依赖、进程、数据库、模型调用；不进 600 ms 轮询（页面打开时才拉）。
不改记忆代码（`memory_agent / veil_brain / brain_sync / experience_memory / recall_*` 一律只读）。
不做自动修复、自动重启、告警推送、历史图表、导出文件、清理策略、多角色聚合、"到点该醒"的 expected 记录。
**复制报告只含服务端脱敏后的技术信息，不提供复制聊天正文或记忆正文的选项。**

## 验收（Lan 在 Windows 上做）

1. 更新重开 → 导航出现「诊断」→ 5 秒内出表，**每行带时间和依据**。
2. 「配置校验」里看到「花园其他」这一组超出 15.5 KB / 11.7 KB，数字与本机工具清单一致，点「复制」能粘出这条。
3. 断网后给 Peter 发一句话 → 聊天里红字 → 「最近异常」出现该条，标明是 HTTP 还是超时；点「这一轮」能看到那一轮，并能打开请求快照。
4. 找一条「被拒绝」→ 有分类标签（如「参数错误（按文案判断）」），不是一句"失败"。
5. 自主活动失败 → 点开看到这次唤醒的模型请求与工具，带 `run_id`。
6. 「复制诊断报告」→ 粘到记事本：含 [1]–[7] 七段、授权拒绝分类统计、配置异常；**不含 API Key、聊天正文、记忆正文**。
7. 关掉 Peter（服务停止），在仓库目录运行 `.venv\Scripts\python.exe diagnostics.py --data-root data --role peter` → 仍然输出一份报告（模块与配置标注为"最后一次快照"）。
8. 重启 Peter → 显示上次启动时间，重启前的异常还在；诊断页读不到时，聊天、自主活动、记忆读写都正常。

## 审查后定点修复（Cubic + Lan，2026-11-24）

只改会导致功能错误、安全问题或明显错误诊断的地方，未扩大范围：

1. 诊断读取失败不再显示成「都正常 / 没有异常」：非 2xx 或没有 overview 数据时清空旧数据，每块明确写「诊断读取失败」。
2. 唤醒 id 带时区 `+`（实测 11,653/11,713 条）被校验拒绝：接口与 Electron 白名单都按真实格式放行，并统一到 `diagnostics.valid_id`。
3. 脱敏补齐：键值形式凭据（`api_key=…` / `password: …`）、URL 里的 `user:pass`、工具日志详情、trace 返回值；单条复制与整页报告同一套。
4. 聊天请求不再继承自主活动的 run_id：归属由该次请求自己的 `autonomy` 上下文决定（`_request_failed(exc, request_id, autonomy)`）。
5. `chat:open-maintenance` 只接受入口 id，目的地从服务端 `/api/diagnostics/links` 取，页面不能指定任意本机端口或路径。
6. run_id / call_id 追踪先解析出该次唤醒自己的 `request_id` 集合，再用它取模型快照；多步请求会全部列出。
7. 只有 run_id 时不再附加无关聊天消息；只有 call_id 时只显示那一次调用（不再因为"空 run_id 相等"把所有行都算进来）。
8. 旧记录没有任何结构化 id 时，`_issue` 强制 `confirmed=false`，页面与报告都写「可能关联，未经确认」。
9. 待批准过期按截止时间判定，单独归类为「批准已过期」，不再显示「等你批准」。

最后一轮收尾（同日）：
- MCP 异常改用稳定 id（服务 + 文案哈希），刷新后同一条仍能单条复制；补了刷新前后 id 一致的回归。
- 单条报告为空时不再提示"复制成功"。
- 数据目录统计到上限时显示「至少 X（只统计到扫描上限）」，不再把部分当总量。
- 系统信息两列布局改到 @@.head@@ 上（原来整行只有 @@.head@@ 一个子元素，值被挤进 76px），并给诊断页补上与其他页一致的页面背景。
- 导航图标 @@stethoscope@@ 在 @@icons.js@@ 里是回退成 more-horizontal 的（看起来和「设置」一样），已补路径。
- 拒绝总数统一为 607（2026-11-24 00:55 快照），并写明差额来源是后续新增。
- 旧版离线快照只写 @@service.role@@，报告回退读取，不再显示 @@?@@。
- 工单与交接文档去掉本机绝对路径；@@chat:open-maintenance@@ 的说明改成 127.0.0.1/localhost。

回归了三个用户场景：花园组超预算（配置校验里可复制）、自主活动 API 失败（run_id → request_ids → 快照链路完整）、权限拒绝分类（含过期与未分类三档原因）。

## 状态

代码完成，本机自动检查：`tests/test_diagnostics.py` 38 条、桌面 23 条、相关回归 436 条（`test_tools` 86、`test_chat` 107、`test_autonomy` 52、`test_request_snapshots` 9、`test_tool_budget` 15、`test_prompt_registry` 32、`test_tool_failures` 19、`test_web` 73、`test_recall_diagnostics` 5；`test_diagnostics` 38 已计入），3 条跳过。**待 Lan 的 Windows 体验验收**；未在正式目录启用，未改记忆。
