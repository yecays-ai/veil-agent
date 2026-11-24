# P2 正式切换报告（2026-11-25）

授权：Lan 明确授权在 PR #439 合并后执行正式 Peter 的 P2 数据治理与读写接管；本次不新增功能、不进入 P3、不卸载 Ombre。

## 1. 切换前检查

- main 已更新到 `0199ec1`（#439 合并提交）。
- 正式 `.env` 的 `EMBED_API_KEY` 有效：1024 维向量、3.9 秒返回，无 401。
- 正式 Peter 停止前健康；停止后无 `web_agent.py`/`ombre_host.py`/`ombre_entry.py` 运行。
- 切换前最终影子同步：`reconcile` changed=0、unexpected=0、source=287。

## 2. 备份与恢复验证（仓库外，未提交）

| 备份 | 用途 |
|---|---|
| `VEIL-backups/P2-cutover-20261125T050617` | 迁移前完整 data/config/brain.db，digest ok |
| `VEIL-backups/P2-cutover-clean-20261125T050737` | 事件完整迁移后、标签清理前，digest ok |
| `VEIL-backups/P2-cutover-final-20261125T050822` | 启用 authority 前完整数据，digest ok |
| `VEIL-backups/P2-authority-20261125T051444` | 最终 authority brain.db + character.toml + 回退导出 |
| `P2-authority-.../brain-before-raw-tags.db` | frontmatter tags 清理前的 brain.db |

恢复验证：从 `P2-cutover-final` 恢复到独立目录 `tmp/p2-formal-restore`，实际启动 Peter：221 条可见记忆（当时值）、72 事件、事件故事 2788 字/2 阶段；读取、短正文修改、版本记录均成功；隔离库 tags=0。测试实例已停止。

## 3. 正式数据治理

- 事件完整数据：正式 `brain.db` 由 P2-B 影子映射升级为完整事件：72 事件 / 73 阶段 / 72 篇故事；
  `event_stages` 从 0 → 73；追加更新行按 event_id 折叠。
- P2-C 标签清理（可逆）：
  - 清理前 2,941 次 tags、2,135 个不同值、1,832 个只出现一次；
  - `tags_json` → `[]`，287 条迁移记录保存在 `tag_migration`；
  - frontmatter `tags` → `[]`，页面/检索不再看到历史自由标签；
  - `domain` 保留（页面分组仍用），正文、ID、创建时间、版本、事件、阶段、来源、关联完全不变
    （清理前后内容指纹一致；仅有 tags 元数据变化）。
- 清理后：287 记忆 / 284 版本 / 171 sidecar / 248 向量 / 72 事件 / 157 来源 / 73 阶段，
  tags=0、pending=0、digest ok。
- 正式数据对账：按 vault + JSONL 逐字段比较 12 组检查，0 mismatch；原有 10 个历史删除 id 仍按已知引用处理，不复活。

## 4. 启用唯一读写权威

`characters/peter/character.toml`：

- `[memory.brain] write_authority = true`
- `[memory.brain] shadow = false`
- `[memory.retrieval] backend = "brain"`

启动时验证：authority 正常挂载、`brain_sync.attach` 返回 None（shadow 不启动）、无冻结文件。

## 5. 正式运行验证

| 场景 | 结果 |
|---|---|
| 健康/页面 | ready=true；222 条可见记忆；tags_nonempty=0 |
| 事件页 | 73 事件；新事件故事可读；旧事件 2788 字完整 |
| 真实写入 | 新记忆 `66320d2a1f76`，事件 `e-20261125-8dc6b0`，故事 495 字，vector ready |
| 事件续写 | 用 target_id 补写同一事件，event/memory ID 不变，故事更新，没有新开事件 |
| 召回 | 真实提问“V02 第一次记忆成功”；`retrieval.backend=brain`、2016ms、8 候选、judge 选中 2 条，回答正确 |
| 重启 | 再次停止/启动后 288 记忆/73 事件/74 阶段、tags=0、pending=0，权威配置保持 |
| 自主活动 | 开关跨 authority 正常（true→false 已验证并恢复为原关闭状态） |
| 双写 | shadow=false；vault 287 个 Markdown 在切换后 0 个被修改；旧 Ombre 无写入口 |
| 桌面 | 正式桌面进程已启动（VEIL.exe）；后端 18011/页面连接正常 |
| Ombre 回退保障 | Ombre 仍运行在 18011，未卸载；当前 Brain 导出 288/286/171/249/73，回导 0 problems；独立 Ombre 能搜到新记忆 |

切换后数据：288 记忆（+1 本次切换验收）、286 版本（+2）、249 向量（+1）、73 事件（+1）、
74 阶段（+1）；原有 287 个 ID 无缺失；pending=0、tags=0、digest ok。

## 6. 回退方案（未触发，已准备）

若正式体验发现严重问题：

1. 停止正式 Peter；
2. 用 `VEIL-backups/P2-authority-20261125T051444/rollback` 中同 ID 导出（已回导 0 problems）或从当前 Brain 重新导出；
3. 将 `character.toml` 恢复为 `write_authority=false`、`shadow=false`、`retrieval=ombre`，Ombre 端口 18011 启动；
4. 启动 Peter，核对切换期新增/修改/删除及事件；
5. 恢复到旧配置前先保留当前 brain.db 备份，避免丢切换期新记忆。

不得用旧备份直接覆盖当前 Brain（会丢 `66320d2a1f76` 等切换期新数据）；回退必须走“导出同 ID → Ombre 读取”。

## 7. 待 Lan 体验验收

1. 打开桌面 Peter，确认记忆页“最近/经历/已归档”可读，标签不再出现历史自由标签。
2. 连续聊 3–5 轮：提到旧事（如第一次记忆成功/砖红）、让 Peter 记一件新事、再追问续写；确认能想起、能保存、不重复。
3. 打开一条事件详情，确认完整故事、阶段、来源和旧事件连续；确认新事件不会把旧碎片重复注入。
4. 锁定/隐藏/归档/恢复/删除各挑一条测试记忆，确认页面操作正常。
5. 观察自主活动开关；确认没有异常费用、没有双份保存、没有“保存成功但检索 pending”的静默情况。
6. 若出现异常，立即停止并告知；按第 6 节回退，不进入 P3。
