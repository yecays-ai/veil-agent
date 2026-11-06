---
name: impact-complete
description: 每个开发任务开始和交付前必读的 VEIL Agent 适配：追踪改动波及的所有功能，连带修复，在原版 Superpowers Skill 的根因定位和验收门槛下工作。
---

# VEIL Agent：跨功能影响检查（项目接线，不是第三方原版 Skill）

本文件只补 VEIL 项目所需的工作流约束。**实际调试与完成声明的成熟方法原样复用**：
- [Superpowers systematic-debugging](../systematic-debugging/SKILL.md)：根因追踪、跨层数据流、复现与验证；配套原版参考文件在同目录。
- [Superpowers verification-before-completion](../verification-before-completion/SKILL.md)：完成前运行证据，防止测试没跑就声称成功。
- 上游：https://github.com/obra/superpowers （MIT，版权与许可见 `../SUPERPOWERS-LICENSE.txt`）。
- 这两个原版 Skill **不保证自动发现所有调用者**；本文件补充影响范围要求。Codex/Claude 是否每次执行，需在后续 PR 中按证据核查，不能假装已有强制代码门禁。

## 每单执行顺序

1. **做前搜全局**：不要只看用户指定的函数。沿改变的功能、配置、状态、数据、接口，向上搜索所有调用者/消费方，向下追踪副作用，横向搜索共享状态；扩展到所有真实入口（聊天、后台、定时、UI、权限、费用、数据、备份、其他角色和错误路径）。**这不是固定模块名单**，本单实际代码出现新路径就继续跟踪。给出文件/函数证据。
2. **按场景形成影响表**：对每个受影响的旧使用旅程，标注当前行为、改后应继续怎样工作、必须联动的修改、验证方式。只有“有代码证据说明不受影响”才能排除；不准用“这单只要求 X”直接排除。
3. **同 PR 联动完成**：变更导致任何原功能失效、被静默降级、预算/审批被绕过、数据不一致，都属于本单，不能留给用户发现后再开工单。涉及用户明确审批的风险/费用，先保留旧功能并说明阻断，不擅自选择。
4. **交付前用原版 Skill 验证**：按 `verification-before-completion` 运行新旧相关测试，检查完整退出码和实际输出；依照最终 Git diff 再查一遍新影响路径。PR 给出影响表、代码证据、实际测试结果与 Windows 用户体验验收步骤。无法实测的平台明确“待验”，不能说已完成。
5. **出现故障用原版 Skill 定根因**：按 `systematic-debugging` 复现、追溯和修复；若影响正确性、权限、安全或数据的回归没解决，不合并。

## 示例（不是只检查这些）

工单 228 的 CC 通道只接聊天，但 `autonomy.py` 的 `chat.send(..., autonomy=True)` 会使用聊天 provider，`veil_agent.py` 的 `_claude_agent_chat` 拒绝自主活动。新通道上线前，应从“共享模型选择”往所有使用者追踪，保住既有自主活动，而非要求 lan 找到问题再开修复工单。此处作为**已知未修代码回归**记录，不能把新增 Skill 说成运行功能已经修好。
