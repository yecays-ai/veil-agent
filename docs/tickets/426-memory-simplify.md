# 工单 426 · 四个 Ombre 功能的数据迁移

## 背景

letters、plans、feel、I 是 Ombre 时代留下的四类数据。正式库里留着 4 封信、6 条计划（active/resolved，
其中 1 条是「花园桌游 HARD RULE」，其实是规则、growth 里已有，正式迁移时用 --skip-plan 跳过，实际迁入
5 条）、7 条 feel 和 3 条 I，以及它们对应的向量行。还有价值的内容要搬出来：信件导成 Lan 能直接读的
Markdown，计划并进 life-records.json 的长期计划；feel/I 删除。

这不是产品功能，是给主 agent 在正式库手动跑一次的数据迁移工具。默认 dry-run，确认后才 --apply。

## 目标

- 信件导出到 `data/<角色>/workspace/lab/letters/2026-10-31-<标题slug>.md`，正文原样；原信件保留。
- 计划迁入 `data/<角色>/life-records.json` 的 `records`：`kind=plan`、`state=completed`，
  标题取正文第一行前 80 字，content 保留正文（去掉首尾空白）；原计划文件保留。行里额外记
  `ombre_plan_id`，重跑按它判重，不会把同名同日期的两条计划合成一条。
- feel/I 的 Markdown 真删，同时删除 `memory/embeddings.db` 中对应 `bucket_id` 的向量行；
  数据库或表不存在时只记日志，不报错。
- `data/session.lock` 且该角色的 `memory/_agent/ombre.lock` 都能独占加锁（VEIL 与 Ombre 常驻都没在跑）
  才执行；任一被占用就拒绝并提示等它们退出。
- 可重复执行：信件不重复导出、计划按 `ombre_plan_id`（旧数据退回 kind+date+title）判重、
  已删除的 feel/I 跳过。

## 改法

新增仓库根目录 `simplify_memory.py`，只用标准库，不联网、不调模型，不改任何运行时模块：

- 参数：`--role`（默认 peter）、`--data-root`（默认 data）、`--apply`、`--skip-plan ID`（可多次）。
- 默认 dry-run：只打印将要做的事，不创建目录、不写日志、不动数据库。
- `--apply` 先备份：把 `data/<角色>/memory` 整棵树和 `life-records.json` 复制到
  `data/<角色>/backups/simplify-<时间戳>/`。先写到 `.partial` 目录，全部复制成功才改名发布，
  失败就删掉半成品并停止，避免留下看着像可恢复、其实不完整的备份。
- 信件文件名去掉 Windows 非法字符和路径分隔符，最长 60 字；同名时追加源 id，重跑内容一致则跳过。
- 计划行字段与现有 life-records 行一致；`date=created` 日期，`time=""`，`repeat="none"`，
  owner/created_by/updated_by 为 Peter；写入用临时文件加原子替换。
- feel/I 先删向量行再删文件，避免中途失败留下孤立向量；数据库被占用或损坏时保留文件并让本次迁移失败，
  等数据库可写后重跑。只删角色 memory 目录内、非符号链接的文件。
- `--apply` 时每一步写 `data/<角色>/memory-simplify-log.jsonl`（`time/action/target/result`）；
  dry-run 不写日志。结束时 stdout 打印简短汇总。

新增 `tests/test_simplify_memory.py`，用临时目录里的假信件、假计划、假 life-records、假 sqlite 覆盖
dry-run 不写、apply 全流程、二次执行无变化、session.lock 被占用时拒绝、Ombre 常驻仍占用锁时拒绝、
`--skip-plan`、同名信件加后缀。

## 验收（lan 在 Windows 上做）

1. 完全关闭 VEIL，并等 Ombre 常驻退出（`data/peter/memory/_agent/ombre.lock` 可独占）。
2. 先跑仓库指定的 Python：`python simplify_memory.py`（默认 dry-run），检查打印的信件、计划、feel/I
   清单和汇总；此时不应出现 backups、导出目录或日志文件。
3. 确认后跑 `python simplify_memory.py --apply --skip-plan ef34e23f2dc5`（跳过花园桌游那条规则）；看汇总里没有错误，并检查
   `data/peter/backups/simplify-*`、`data/peter/memory-simplify-log.jsonl`。
4. 打开「生命记录」页：应有 5 条已完成的长期计划；`workspace/lab/letters/` 有 4 封可直接读的信；
   feel/I 文件已消失，原信件和原计划文件还在。
5. 再跑一次 `--apply`：计划数量不变、导出信不重复、汇总里显示跳过。
6. 需要恢复时：关闭 VEIL，把 `backups/simplify-*/memory` 覆盖回 `data/<角色>/memory`，把
   `life-records.json` 拷回 `data/<角色>/`。
