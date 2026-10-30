# 工单 171：Peter 自主探索本地普通文件

## 目标

Peter 可在聊天或自主唤醒时浏览 Desktop、Documents、Downloads、Pictures 的可见普通文件，按名称查找，并分段阅读 TXT、Markdown、DOCX、可提取文本的 PDF；JPG/PNG 仍可查看。Lan 可在现有活动记录检查路径、动作与结果。

## 当前状态

沿用 `desktop_files.py` 的只读权限、路径检查和统一工具日志；原有写文件权限没有扩大到 Downloads。DOCX 复用 MIT 许可的 [python-docx 1.2.0](https://pypi.org/project/python-docx/)，PDF 复用 BSD 许可的 [pypdf](https://pypi.org/project/pypdf/)；两者可在 Windows 使用。输入与单次输出有上限，长文档按字符位置继续阅读。不做 OCR 或全盘搜索。自动检查与 Windows 实机体验分别记录在 `STATUS.md`。

## 验收步骤

1. 在 Documents 放普通 DOCX、可选中文字的 PDF，等 Peter 自主醒来后让他自己发现并读一段真实内容；长文档试一次继续阅读。
2. 让 Peter 浏览 Downloads、Pictures 和 Desktop，再尝试一个授权目录外的路径，确认被拒绝。
3. 打开现有活动记录核对浏览、查找、读取及结果，重启后确认原文件未改动。
