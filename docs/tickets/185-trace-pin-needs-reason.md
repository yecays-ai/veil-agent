# 工单 185：trace 置顶也要理由

## 背景
Peter 11-01 实测：hold 置顶会要求 why_remembered，但 trace(pinned=1) 没要求，重要度直接变 10。

## 改动
新置顶时 trace 必须带非空 edit_reason，否则拒绝，不调用 Ombre。

## 验收
让 Peter 对一条普通记忆 trace pinned=1 且不写 edit_reason，应被拒绝；写了理由后才能置顶（验完请让他取消置顶并把重要度设回去）。
