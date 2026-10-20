# 交接

更新：2026-10-20 16:42 +08:00 · Claude · 撤销 017 气泡拆分

## 做到哪

- 本轮基线 main `a7910f9eca7a74998963dc0260d0b63490d6ff4d`。分支 `claude/project-thread-shdu13` 撤销 017：`web/index.html` 恢复为 PR #35 前的内容（与 `aafc121~1` 逐字节相同），工单 017 标为「已撤销错误实现，原需求待重新设计」。PR #35 未新增测试，故无测试可删。本机 `tests.test_web`、`tests.test_chat` 47 项中仅 2 项因沙箱缺 `openai` 包报错，改动前 main 上相同；页面脚本 `node --check` 通过。Windows 真实体验待 lan 验收。

- 本窗口基线 main `aafc12139abefc76f057956ec96689923e0a7fcf`；随后 PR #36 已合并，当前 main 为 `c5baaa6f9e82d9d955d839637fec9e42ec95002c6b`。015 未验收；014 待验收；013 网页已验收。权威状态与体验记录见 `STATUS.md`。
- 016 / 4A 真实 Windows 体验已由 lan 报告通过：重启后可读、可创建文件和目录、覆盖和越界拒绝、v1/v2 完整、成长日记已自主追加、无脚本执行入口。开发端没有独立证据核对具体路径/提示、文件全文和字节、日记逐字节变化或脚本拒绝提示，见 `STATUS.md`。
- 写后 SHA-256 核对、Windows 保留名/非法字符/大小写重名/路径长度检查、符号链接与 junction 隔离、硬链接追加拒绝、超限零写入和 `vN.md` 两版字节数提示已实现。能力变化复用 capabilities.json 和既有提示机制。
- PR #35 已由 GitHub CI 通过并由 CodeRabbit 完成评审（无可操作评论），已合并，merge commit `aafc12139abefc76f057956ec96689923e0a7fcf`。4D Windows 网页真实体验仍待 lan 验收。
- PR #36 已合并：Peter 的 `max_steps` 设为 12；未执行的 raw DSML 工具标记改为明确提示。预算硬上限及现有工具调用安全路径不变。GitHub CI 四项通过，CodeRabbit 对实现代码无可操作评论。lan 的真实 Windows 验收尚未完成，步骤见 `STATUS.md` 和 `docs/tickets/018-peter-tool-budget.md`。
- 当前工作目录是只读项目镜像，没有本机检出目录；未新增或运行自动化测试。PR #36 GitHub CI 四项通过，CodeRabbit 无可操作评论；自动检查不等同于 Windows 体验验收。
- 当前正常启动工作区的本机配置修改未纳入本工单；施工使用隔离工作树，避免打断本机聊天。

## 下一步

合并撤销 PR 后，请 lan 按 `STATUS.md` 的「017 撤销验收」和 018 步骤在 Windows 验收。多条独立消息/主动消息的审计结论由 Claude 单独提交，lan 确认方案前不开发新消息系统。

## 注意

- 015：隐藏可 `/restore`，已删除的记忆不能通过 `/restore` 恢复。不要改变删除语义。
- lab 根路径是角色 workspace，工具参数写 `lab/...`。成长日记保留在 workspace 根目录，持续授权与 lab 权限互不替代。
- 016 只包含实验室；脚本执行入口仍为空。017 的气泡拆分已撤销，不要再按句子在网页上拆分回复。018 只改变 Peter 的工具步骤上限及 raw DSML 文本收尾，保持 ¥10/日、¥300/月预算及原工具权限。
- 012 缓存效果、014、015 及 011 剩余场景仍以 `STATUS.md` 的用户验收记录为准。
