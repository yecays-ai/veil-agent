# 工单 239：工作台的本地开关

## 为什么
工作台默认关闭，开启要改仓库里的 `characters/peter/character.toml`，而改了被跟踪的文件会挡住 `update.bat`。lan 因此没法真正用上它。

## 做了什么
- 开关放在 `data/peter/workbench.json`（`data/` 不进仓库，更新不会丢）。只认三个字段：`enabled`、`project_roots`、`container_image`；其他字段（预算、步数、命令路径）仍只能在 `character.toml` 里设。文件坏了或字段不对，Peter 启动会明确报错，不会悄悄忽略。
- 双击 `workbench-switch.cmd`（`workbench_switch.py`）：开启、关闭、加项目文件夹、清空。开启时检查 Docker 里有没有 `veil-workbench` 镜像，没有就当场构建；Docker 不可用就不开启。项目文件夹必须存在，且不能包含或位于 Peter 自己的 `data` 文件夹。改完重开 Peter 生效。
- 开启后仍是容器模式，改前自动快照，联网/安装/推送问 lan；见工单 238。

## 没做
- 设置页里的开关和选文件夹界面（界面改动在 lan 本地 Codex，这里没碰）。
- 自主醒来在真机上的验收；Linux 主机未验。
