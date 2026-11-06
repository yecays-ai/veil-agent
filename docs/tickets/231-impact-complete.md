## 目标
每次改动 Peter、Ghost 或共享功能前后，都追踪真实使用路径；修复本次改动引起的连带故障，并给出回归证据。

## 当前状态
`AGENTS.md` 与 `.agents/skills/ticket/SKILL.md` 已接入 `.agents/skills/impact-complete/SKILL.md`。该项目 Skill 复用 MIT 许可的 `systematic-debugging` 和 `verification-before-completion`；修正了示例中的错误路径、缺失引用和不适用于本仓库的命令，并补上可填写的影响矩阵。PR #283 同步到 PR #284 合并后的 main，审查意见一次性修复；检查状态待本提交 CI。已知的 CC 聊天与自主活动模型耦合属于既存运行缺陷，不由这份流程文档引起，将在下一张模型选择工单中修复。

## 验收步骤
1. 在后续代码改动中，检查 Codex 是否先列出聊天、工具、权限、后台活动及其他角色的相关调用证据。
2. 检查交付 PR 是否填写影响矩阵、运行相关回归验证，并区分 CI 与 Windows 实机验收状态。
