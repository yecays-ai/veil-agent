# 031 普通审计证据附件

基准：`bbfaa5c392c6e643214f4decc1fcf729f2b2ce24`。只记录虚构数据和工具汇总；不包含私人文件、原日志或高危漏洞请求。问题解释、等级和范围以 `031-repository-audit.md` 为准。

## 测试与扫描

| 检查 | 结果 |
|---|---|
| Python 主离线套件 | 228 项：224 通过、1 失败、3 跳过 |
| 失败项 | `test_web.WebTests.test_explicit_exit_stops_new_submissions`：HTTP 返回 200 后 `hub.stopped` 仍为 False；独立复跑也失败 |
| 跳过项 | 真实 Ombre adapter、Ombre 固定召回门、真实 filesystem MCP |
| 实验预算代理离线测试 | 8/8 通过 |
| Ruff 0.17.1 isolated | 57 条候选，人工复核，不作等量 Bug 计数 |
| Bandit 1.9.4 | 50 条：9 medium、41 low；0 high |
| Vulture 2.16，confidence >=80 | 8 条候选，主要是假对象参数及未用变量 |
| Gitleaks 8.30.1，全部本地 Git refs，100% 脱敏 | 125 commits / 约 1.63 MB，0 命中 |

可复用的扫描命令（工具由独立临时环境提供；不要扫描私人目录）：

```text
python -m unittest discover -s tests -v
python -m unittest discover -s experiments/memory-quality-017 -p test_budget_proxy.py -v
python -m ruff check --isolated --no-cache --select E4,E7,E9,F,B,S --output-format json <git ls-files 中的全部 Python 文件>
python -m bandit -f json <同一文件集合>
python -m vulture <同一文件集合> --min-confidence 80
gitleaks git . --log-opts=--all --redact=100 --no-banner --report-format json
```

本轮明确设置 `OMBRE_ADAPTER_GATE=0`、`OMBRE_VECTOR_GATE=0` 并移除 `OMBRE_SOURCE/OMBRE_PYTHON`，避免可选测试读取真实密钥/总账。主测试初次遇到沙箱 socket 权限限制；获准 loopback 后重跑，记录上述最终结果。扫描首轮继承外部 Ruff 配置，未用它作最终统计；最终统计来自 isolated 规则集合。

## 非破坏性探针

探针只新建 `TemporaryDirectory`，使用假角色、假模型、现有测试 Backend；不调用 `main`、真实 `open_ombre` 或供应商接口。可按以下步骤在临时脚本重现，不必改正式测试。

### 031-02：多 assistant 消息与记忆游标

1. 临时 Store 写入一条 user、一条 assistant。
2. 创建并关闭 `Memory`（native、假 Backend），其初始 cursor 为 1。
3. 向同一临时 chat.jsonl 再追加一条 assistant，模拟已持久化的主动留言。
4. 用同一 Store/角色重新构造 Memory。

实际：抛 `UserError: 记忆调度记录损坏，已停止；请保留记忆库，不要删记录。`。文件未被删除。`Memory.turns()` 仅接受恰好两条消息的组，与 `Chat.recent()` 的多消息语义不同。

### 031-03：外部隐藏后开场缓存仍出现

1. 假 Backend 预置一条虚构记忆，构造 Memory，让开场载入它。
2. 在 Backend 中将其隐藏，模拟独立 Ombre 面板写入，不执行正式隐藏 API。
3. 重新生成 `memory.context()`。

实际：搜索结果不含该记忆，但开场 system 消息仍含原正文。本地 `entries.hidden` 未同步，就无法过滤开场。这个探针证明包装层缓存/过滤缺口，不是完整的 Dashboard 集成测试；面板写入路径由固定上游源码复核。

### 031-05：40 条进度后无法完成

1. 临时 LongTaskStore 建立一个假任务。
2. 连续提交 40 条有效 progress_update。
3. 以正确 task_id 提交 `status=completed` 和最终证据。

实际：`被拒绝`，状态仍 active；提示“进度证据已达保存上限；请先用 progress_update 总结已有证据后再继续”。按提示总结也走同一拒绝条件；不带证据完成亦被拒绝。

### 031-06：核实太宽或无法匹配

- 临时任务记录 `append_file` 的 pending 目标 `lab/result.md`、结果不确定。在后续步骤仅观察 `list_directory` 对 `lab` 的“成功”，下一步提交 resolve_pending，即可清空 pending；没有读取文件正文。
- 另建任务，记录 `trace` 目标为原生工具 preview 的 `trace`；返回显示标题后，观察真实格式 `memory_history` 摘要“历史：Fictional title（1 个旧版本）”。后续 resolve_pending 仍拒绝，目标未匹配。

两者来自同一根因：用显示文字和包含关系代替稳定目标/操作证据，故合并为一条发现。

### 031-07：能力清单并发事务

临时能力文件包含待通知事件且 autonomy_announced=false。两个线程分别执行 `consume_capability_event`、`announce_autonomy`，只对 `os.replace` 安排可发生的交错：前者写完 temp 后等待，后者改写同一个 temp 并 replace，再释放前者。

实际：consume 抛 UserError，实例 `enabled=False`。探针没有伪造磁盘故障；只是固定两个真实读改写事务的执行顺序。仅生成独特临时文件名仍不能解决丢失更新，必须串行化整个事务。

## 虚构冷恢复演练

在一个全新临时实例创建 13 个文件，未使用实际人物内容或有效 API key。保存/关闭写入者后复制到另一临时目录。

```json
{
  "readme_backup_missing_character_config": true,
  "full_cold_copy_equal_sha256": true,
  "file_count": 13,
  "chat_messages_reloaded": 2,
  "hidden_filter_restored": true,
  "task_id_restored": true,
  "unresolved_effect_restored": true,
  "activity_enabled_restored": true,
  "cost_totals_equal": true,
  "pending_cost_retained": true,
  "copied_append_grant_identity_still_matches": false
}
```

恢复方法：完整复制 characters、persona、data 和假 .env；使用恢复目录的 `load_character`、Store、Budget、Memory（假 Backend）、Chat、LongTaskStore、ActivityState 重新读取。费用包含一次结算和一次未结算预留；任务含未核实副作用；memory state 含隐藏项；Lab/日记和 persona/config 均纳入哈希。

13 文件相等在重新打开会追加状态前核对。SQLite 文件是自行构造的虚构索引，用于检查二进制文件复制，不是 Ombre 的真实 schema。**未证明真实 Ombre 索引查询、热备一致性、Dashboard 凭据迁移、Windows ACL 或另一台电脑可启动。** 仅按 README 的 data/persona/.env 三项恢复时，角色配置缺失导致 load_character 失败。

复制后的文件身份改变使旧追加授权不再匹配，这是安全行为，应重新批准，不能修改身份检测来免批准。

## 固定第三方源码核验

- Ombre 固定提交：`115d831d979bf67efc7150f976e2cb34c333c935`，归档 SHA-256：`a4226e051e39c6bbe434b36951a9b24a74ff708577e2bd91a5502dd507737e43`。
- 对 `src/server.py`、`server_app.py`、`tools/trace/core.py`、`decay_engine.py`、`ombrebrain/storage/embedding_outbox.py`、`web/buckets.py` 与归档逐字节核对，均一致；安装包 LICENSE 为 MIT。
- 上游费用检查不是动态供应商计费测试：读到打标输出上限 4096、内部最多三次请求尝试、额外事件/计划判断和自然衰减补向量路径；父层只估算固定量，没有通过请求边界约束所有子进程消费。
- 上游 server.log 已使用 1,000,000 字节轮转并保留 3 个历史文件；本报告没有写成“所有日志都没有轮转”。

P0 的独立私密证据不放在此附件或任何公共 PR 中。Claude 复核时需要同时查看本机私密附件；不能只阅读公共摘要后将 P0 标记已复核。
