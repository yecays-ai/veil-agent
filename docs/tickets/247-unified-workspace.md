# 工单 247：统一 Peter 的工作空间，淘汰旧 Lab 限制

## 目标
Peter 只有一个文件根：`data/<角色>/workspace/`。`lab/` 是其中一个分类，不再是另一套文件系统和权限制度；工作台（OpenCode）也在同一个文件夹里干活。lan 不选目录、不读命令。

## 现状（改完后）
- 直接文件工具（`write_file`、`append_file`、`create_directory`、`move_lab_file`、`lab_file_digest`，新增 `edit_file`、`delete_file`）在整个 workspace 里免确认可用：能覆盖、原地编辑、移动、删除自己的文件。覆盖、编辑、删除前，程序把旧版本存到 `data/<角色>/own-work-versions/`（lab 里沿用 `lab/`，其余在 `workspace/`），可恢复。
- 旧的"只能在 lab 里、不能覆盖/编辑/删除、只能搬进 archive/trash"规则已删除。例外只有成长日记：只能追加，仍有独立授权。
- 工作台在没有项目匹配时默认在 workspace 里工作；改前尝试留快照（不问 lan，它是 Peter 自己的空间）；容器里日记被只读屏蔽。旧的 `data/<角色>/workbench/` 在第一次使用工作台时整个搬进 `workspace/from-workbench/`（不覆盖、不删除；同名保留原处）。
- HTML 预览、小游戏、发送附件改为 workspace 相对路径（如 `lab/game/index.html`）；游戏分数沿用原来的 game_id。`run_lab_python` 仍限 `lab/` 内（离线容器沙盒），`lab = false` 只关实验室功能，不影响 workspace 权限。
- 仍保留：lan 在 Peter 空间之外的文件不动；密钥/`.env`/`.ssh`/浏览器密码挡住；预算上限、人工接管。

## 验收（lan）
1. 更新并重启 Peter。对他说："把你之前在 lab 里写的某个文件改一下再删掉一个没用的"。他应直接改，不再问你，也不说"没法覆盖/删除"。
2. 说"你把工作台里以前做的东西整理一下"，再连续让 Peter 用工作台完成三次任务；`workspace/from-workbench/` 里应有以前 workbench 的文件，任务正常结束且 Windows 不弹黑窗。停止一次任务后确认没有遗留进程。
3. 让他做个小网页或小游戏再预览、发给你，仍能打开；确认成长日记不能被覆盖或删除。

## 没做
- 旧 `run_lab_python` 沙盒、桌面文件工具（`desktop_create_file`/`desktop_move_to_trash`）保持原样；等工作台经真机验收覆盖后再按工单 240 的条件删除。
- Windows 真机未验。
