# 交接

更新：2026-10-19 13:30 UTC · Claude

## 做到哪

- 006（文件工具）经 008 修复后，lan 在 Windows 上六项验收全部通过。
- 007 已实施在分支 `ticket/007-ombre-native`：`memory_native.py` 给 Peter 两个 Ombre 工具（`hold`、`breath_search`），走 006 的工具循环和预算；`[memory] mode = "native" | "extract"` 一行切换新旧方案，旧补记层一行没删。`Memory.record_native` 把 Peter 自己存的条目登记进 `state.jsonl`，所以 `/memory` `/forget` `/delete` 照常。新增 `tests/test_native_memory.py`（10 项，假后端）。
- 本机沙箱没有 npm 网络，`test_real_filesystem_server_lists_only_the_sandbox_folder` 在这里本来就失败（主分支上同样），以 CI 的 Windows 结果为准。
- `characters/peter/character.toml` 的 `thinking="enabled"` 是 lan 的本机未提交设置，不要纳入 PR（本 PR 只改了 `[memory]` 加 `mode`）。

## 下一步

lan 用真实对话对照新旧方案（步骤见 `STATUS.md`）。达标后另开工单移除旧补记层（`memory_agent.py` 的 extract/supplement、相关测试和 README 段落）；不达标就先列差距。不要在她验收前删旧代码或给旧补记层加东西。

## 注意

- 文件工具只允许角色工作区；文件读取结果和路径不要写进长期记忆。写入必须先询问，且只能独占新建。
- 005c–005g 的真实聊天验收仍待 lan。费用相关取舍（限制保存次数、关 Ombre 模型等）先问 lan，并说明每天多/少多少钱。
