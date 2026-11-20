# 指令登记表（工单 400 / 401 / 402）

模型能看到的所有文字都从这里来。代码里不再写长句：

    import prompts
    prompts.text('autonomy.wake.rules')

## 三层

| 层 | 位置 | 谁会改 | 更新时 |
|---|---|---|---|
| 默认文本 | `prompts/defaults/<编号>.md` | 开发者（仓库内） | 随 git 更新 |
| 你的覆盖 | `persona/instructions/<编号>.md` | 你在软件里改 | 不动（不进仓库） |
| 登记表 | `prompts/registry.py` | 开发者 | 随 git 更新 |

请求时：有覆盖用覆盖，没有就用默认。整条替换，不留版本。

## 两种登记条目

- `text`：文本，可在「指令」页直接改；`editable=False` 的只读（协议、安全）。
- `marker`：每轮由程序生成的数据（时间、召回的记忆、上次活动），没有固定原文，只显示。

人设、背景、说话样例、他记的你、你的设置**不登记在这里**：它们各有自己的页面。

## 工具说明：按类管理，不逐条登记

每个工具的说明都进模型。工具会不断新增，所以它们不写进 registry，而是由 `prompts/tools.py` 统一管理：

- 「指令」页读取这个角色实际提供的工具，一个工具一行，标题是他看到的名字（别名），可改可存。
- 你的修改存在 `persona/instructions/tool.<工具名>.md`；没改过就用代码里的默认。
- 新增工具不需要登记，页面自动出现。

## 防写死

`prompts/inline.py` 扫描模型相关模块里的长英文句子：

- 工具说明（`description=`、`OMBRE_DESCRIPTIONS` 这类表）自动豁免：它们已被工具层管理。
- 其余长句必须在 `prompts/inline_feedback_allowlist.txt` 里，并带类型化原因
  （`feedback:` / `tool-param:` / `git:` / `record:`）。
- 这份名单**只许减少**（测试基线 `inline.BASELINE`）；新增一段提示词必须放进 defaults，
  往名单里加一行会失败。

## 新增一条提示词（唯一正确做法）

1. 在 `prompts/defaults/` 新建 `<分类>.<名字>.md`，写默认文本；需要动态值就写 `{name}` 占位符。
2. 在 `prompts/registry.py` 的 `ENTRIES` 里加一条 `_text(...)`（分类、中文名、注入位置、上限）。
3. 代码里把原来的字符串换成 `prompts.text('<编号>')`；动态部分用 `prompts.fill(template, values)`。
4. 跑 `python -m unittest discover -s tests -p "test_prompt_registry.py"`。

## 分类

identity 人格与身份 / voice 说话方式 / background 背景资料 / memory 记忆 /
autonomy 自主活动 / tools 工具 / system 系统与安全 / runtime 运行时
