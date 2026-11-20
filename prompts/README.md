# 指令登记表（工单 400）

模型能看到的所有文字都在这里登记。代码里不再写长句，只引用编号：

    import prompts
    prompts.text('autonomy.wake.rules')

## 三层

| 层 | 位置 | 谁会改 | 更新时 |
|---|---|---|---|
| 默认文本 | `prompts/defaults/<编号>.md` | 开发者（仓库内） | 随 git 更新 |
| 你的覆盖 | `persona/instructions/<编号>.md` | 你在软件里改 | 不动（不进仓库） |
| 登记表 | `prompts/registry.py` | 开发者 | 随 git 更新 |

请求时：有覆盖用覆盖，没有就用默认。覆盖是整条替换，不做版本、不做差异、不做恢复默认。

## 三种条目

- `text`：文本，可在软件里改（`editable=True`）；`editable=False` 的只读。
- `file`：指向本机文件（人设、背景、样例、记忆笔记），在原来的页面改，这里只显示。
- `marker`：每轮由程序生成的数据（时间、召回的记忆、上次活动），没有固定原文，只显示。

## 新增一条提示词（唯一正确做法）

1. 在 `prompts/defaults/` 新建 `<分类>.<名字>.md`，写默认文本；需要动态值就写 `{name}` 占位符。
2. 在 `prompts/registry.py` 的 `ENTRIES` 里加一条 `_text(...)`（分类、中文名、注入位置、上限）。
3. 代码里把原来的字符串换成 `prompts.text('<编号>')`。
4. 跑 `python -m unittest discover -s tests -p "test_prompt_registry.py"`。

不要直接把长句写进代码：`tests/test_prompt_registry.py` 会扫描模型相关模块，发现未登记的长句就失败。
确实必须留在代码里的（例如协议片段），运行 `python -m prompts.inline --update` 写进
`prompts/inline_allowlist.txt` 并写明原因；这份名单只能缩小，不能随手扩大。

## 分类

identity 人格与身份 / voice 说话方式 / background 背景资料 / memory 记忆 /
autonomy 自主活动 / tools 工具 / system 系统与安全 / runtime 运行时

## 已知边界（工单 400 只做第一部分）

- 工具说明逐条可改在工单 401（本单先做了发消息、沉默、稍后回、撤回四条）。
- 写死但还没搬进登记表的长句列在 `prompts/inline_allowlist.txt`，是下一单的清单。
- 覆盖文件的读取每次请求都做，所以保存后下一次请求生效，不需要重启。
