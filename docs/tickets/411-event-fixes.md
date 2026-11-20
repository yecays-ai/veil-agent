# 工单 411：事件记忆的三个数据安全缺陷

- 格：A（记忆与人格连续性）
- 做完 lan 能多做的一件事：迁移预览不会再写到旧记忆库里；故事校验能认出 `100` 被写成 `1000`；`link_to` 保存也会带上他当时的理解。
- 她怎么验收：用预览时把 `--out` 指向记忆目录，应被拒绝；故事里把数量改大，校验应失败；用 `link_to` 保存时写“我当时以为”，详情里能看到。

## 目标

1. `event_migration.write_manifest` 拒绝写入 `data/<角色>/memory`；先写临时文件再原子替换。
2. `EventMemory._missing_facts` 对数字做精确 token 匹配，`100` 不被 `1000` 满足，`100.5` 也不等于 `100`。
3. `hold(link_to=..., interpretation=...)` 把理解传给事件阶段。

## 当前状态

- `write_manifest` 直接 `open('w')`，未保护输出路径。
- 事实校验用 `value not in story`，数字会被子串误判。
- `_hold` 的 `link_to` 事件分支没传 `interpretation`。

## 验收步骤

1. `event_migration.py --out <角色记忆目录>/x.jsonl` 应报错且不创建文件。
2. 阶段事实含 `100`、故事写 `1000` 时 `_missing_facts` 返回 `['100']`。
3. `hold` 带 `link_to` 与 `interpretation`，新阶段有该理解。
