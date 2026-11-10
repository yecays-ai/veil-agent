# 工单 337：docs: add path-normalization guard note to docs/

## 目标

Lan can open a new docs/ note in the veil-agent repo that states one small, useful rule borrowed from sindresorhus/slash's path normalization: guard the Windows path normalization by the exact extended-length prefix, and leave everything else untouched.

## 当前状态

分支 `ticket/337-docs-add-path-normalization-guard-note-t`，从 `main` 的 `fe2deb35` 建立；开发在独立 worktree 中进行，正式运行目录未改动。

## 验收步骤

['Open the new docs/ file in the test instance and confirm it contains exactly the one-line note about the \\?\\ prefix guard and that \\\\.\\ device paths are not covered by it.', 'Confirm the change adds only that one docs file and touches no source code.']
