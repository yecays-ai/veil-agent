# P2-B 自审（2026-11-24 收尾）

## 已完成（隔离/实机）

- **页面兼容**：实际启动 Electron 桌面端，17/17 步骤通过：列表、搜索、详情、修改、历史版本、
  版本恢复、锁定/解锁、归档/恢复、隐藏可见性、删除、事件详情；panel/events JSON 字段兼容。
  证据：仓库外 `p2-439/iso/evidence/desktop/desktop-e2e-result.json` 与 30~40 截图。
- **读取接管**：自动召回/搜索/判官/assisted/页面/事件均走 Brain；Brain 读取异常时降级到旧 backend，
  不再把空记忆静默返回。
- **黄金问题集**：50 条真实聊天旧注入问题，top-8 对比；Brain 命中 29/50，Ombre 16/50；
  期望记忆覆盖率 36.0% vs 22.8%；延迟 187 vs 517 ms；4 条 Ombre-only 残留已列出。
  8 条无答案问题最高绝对余弦 0.535；真实聊天“年糕的猫”judge=empty、injected=[]。
- **连续性**：真实聊天旧事件续写保持同一 event/memory ID；新经历新建 ID；V02 2428 字完整故事
  进入最终发送上下文（truncated=false）；连续追问承接上一轮；无同 ID 重复注入，事件与碎片不同时注入。
- **失败降级**：索引失败→pending+明确提示+可重试；写入失败→报错且无半条；检索异常→fallback 且继续回答；
  embedding 超时 20.7s→fallback。修复了只读 brain.db 导致读取崩溃的问题（读取连接改为只读、pending 删除账本容错）。
- **旧机制收敛**：实际跑后台经验整理（写入 Brain，第二次不重复）、合并归档、归档恢复、锁定保护；
  shadow 与 authority 互斥；旧 Ombre 写入 0 次，vault Markdown 未变。
- **切换/回退**：冻结拒绝写入→在线备份 digest 一致→导出 0 problems→pending/篡改均中止→
  单写权威切换新增/修改/删除→重启保持→独立 Ombre 找到修改、硬删不复活、Ombre 上 save/modify/search 成功。
- `test_recall_*.py` 234 条、`test_web.py`+`test_veil_brain.py` 83 条通过。

## 未完成/限制

- 正式 Peter 切换未执行（需 Lan 授权与真实体验）；正式库未切换。
- 正式 `.env` 的 `EMBED_API_KEY` 当前 401；隔离验收使用备份 key，正式切换前必须更新。
- 记忆页没有独立“隐藏/取消隐藏”按钮（页面改版归 #431）；隐藏/恢复在数据层与 Peter 工具层已验证。
- 黄金集无人工“重要度”真值；4 条 Ombre-only 案例需 Lan 体验复核。
- 真实聊天/记忆正文、密钥与原始证据只在仓库外。

## 结论

P2-B 的隔离读取、页面、质量、连续性、失败、旧机制、切换与 Ombre 回退验收已完成；
正式切换和 Lan 的真实使用验收不在开发方隔离验收范围。
