# 交接

新窗口或新 agent：按顺序读 `AGENTS.md` → `STATUS.md` → 本文件，然后接着做 `STATUS.md` 里的“下一步”。

## 上一位停在哪

- 2026-10-18，Claude：协作文件已合并（PR #1，工单 001）。
- 工单 002 写在分支 `ticket/002-text-chat` 上，还没开始实现，也还没开 PR。下一位从这个分支接着做，实现完开 PR。

## 背景（只写仍然成立的）

- 旧 veil-agent 仓库已删除重建，旧代码不再使用；需要时只把思路当参考。
- 记忆用 Ombre Brain（MIT）；语音用 Wyzer 的语音模块（openWakeWord、faster-whisper、Kokoro，MIT）；桌面形象用 ARPA avatar（MIT）。依据见 [参考仓库调查](https://claude.ai/code/artifact/25befcb0-c4db-4927-a29b-cc459951d1ba)。
- fount 不用：公开协议只允许阅读，运行和修改需要作者书面授权。
- Ombre 的“每轮自动想起”接口：`breath_search(mode="automatic")`；对话开场用无参 `breath()`。

## 注意

- CodeRabbit 是否已在新仓库启用，以 PR 上有没有它的评论为准。
