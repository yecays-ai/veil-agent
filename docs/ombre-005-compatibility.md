# 工单 005：Ombre 前置验收结果

2026-10-19 · Codex。**安装/启动通过，聊天句子的自动召回未达标；按工单第 3 项暂停接入。** 本分支只增加可复跑的前置检查，长期记忆尚未实现。

## 固定来源和环境

- [Ombre-Brain](https://github.com/P0luz/Ombre-Brain/tree/115d831d979bf67efc7150f976e2cb34c333c935)，MIT，提交 `115d831d979bf67efc7150f976e2cb34c333c935`；没有修改或复制其实现代码。
- 上游源码 ZIP SHA-256：`a4226e051e39c6bbe434b36951a9b24a74ff708577e2bd91a5502dd507737e43`。ZIP 不带 requirements.txt，安装所用文件从同一提交的文件接口单独读取。
- Windows Python 3.11.9 / 3.12.14，各自隔离安装；MCP 客户端和服务端均使用对应解释器。3.11 按上游 requirements.txt 范围解析，3.12 用上游 requirements.lock.txt。3.11 的 jieba 用 3.12 预先构建的通用 wheel 安装，未改库代码。
- 3.11：mcp 1.30.0；3.12：mcp 1.29.0；两边 jieba 0.42.1、rank-bm25 0.2.2 均安装并显式导入通过。
- 官方 MCP SDK stdio_client / ClientSession，真实 Ombre 子进程、临时 Markdown 记忆库。先写入，关闭进程，再以同一临时库重启后查询。
- 子进程只传必要的 Windows/编码/临时目录环境变量；不继承供应商密钥。dehydration.api_key 为空；embedding.enabled=false / api_key 为空；github.enabled=false；只用 stdio。不读项目 .env，没有调用付费模型、向量或标签服务。
- 8 条虚构记忆（6 条普通、2 条情感），正文均中英双语，附中英关键词和近义说法、原文 quotes / source_content。情感桶按上游要求先建立 source_bucket，再 hold(feel=True)。没有用 test_data=True 改变检索行为。

## 固定样例结果

指标 Recall@3：预期条目的 ID 是否出现在 `breath_search(query=<整句>, mode="automatic", with_ids=True, max_results=3)` 的**检索命中**前三条。随机“联想浮现·非检索命中”不算成功。先触发索引构建，等 3 秒再测量，排除首次空索引影响。

Python 3.11 和 3.12 结果相同：

| 类别 | 查询 | 预期 | 结果 |
| --- | --- | --- | --- |
| 中文问英文聊过的事 | 下周四的面试我有点紧张 | 工作面试 | 未命中 |
| 中文问英文聊过的事 | 猫咪周六去兽医那里体检 | 猫的体检 | 未命中 |
| 中文问英文聊过的事 | 下周日的陶艺课 | 陶艺课 | 未命中 |
| 英文问中文聊过的事 | What tea do I like? | 茉莉花茶偏好 | 未命中 |
| 英文问中文聊过的事 | Walking by the river after dinner | 晚饭后河边散步 | 命中 |
| 英文问中文聊过的事 | When is my sister's birthday? | 妹妹生日 | 命中 |
| 换说法 | 最近找工作，想练习一下求职 | 工作面试 | 未命中 |
| 换说法 | I need to unwind with a stroll | 晚饭后河边散步 | 未命中 |
| 换说法 | I want to buy a gift for my sister | 妹妹生日 | 未命中 |
| 情感 | 最近搬家后还是很孤独很想家 | 搬离家后的孤独 | 未命中 |
| 情感 | I'm still proud of finishing my thesis | 完成论文的骄傲 | 未命中 |

- 普通记忆 **2/9（22.2%）**，情感 **0/2**，合计 **2/11（18.2%）**。
- 对照：直接问 interview、jasmine tea、pottery，3/3 命中；记忆已落盘、可检索。
- 无关查询 volcano submarine：结构化命中 ID 为空。
- 原生隐藏/恢复：trace(bucket_id=<茶偏好编号>, dont_surface=1) 后，jasmine tea 不再命中；改回 dont_surface=0 后再次命中。不删 Markdown，可撤回。
- 这是本机依赖测试，不是 CI 或真实聊天验收。小样本不能推算所有聊天的召回率，但必要场景已未通过。

## 已定位的契约差异

1. [bucket_manager.py](https://github.com/P0luz/Ombre-Brain/blob/115d831d979bf67efc7150f976e2cb34c333c935/src/bucket_manager.py) 的 BM25 后台懒重建，默认融合阈值 50。装分词依赖、双语正文和近义词并不保证整句查询过门槛。本测试没有降低默认门槛来凑召回率。
2. [search.py](https://github.com/P0luz/Ombre-Brain/blob/115d831d979bf67efc7150f976e2cb34c333c935/src/tools/breath/search.py) 的 search_query 明确排除 feel/plan/letter。因此仅用指定 breath_search 不能拿回 feel=True 情感桶。向量服务本身不能取消类型过滤；是否补充原生情感检索入口，需要调整接入契约。
3. hold(feel=True) 除 source_content 还要求已存在的 source_bucket，返回情感编号形如 feel_...，不是普通桶的 12 位 hex。
4. 搜索输出没有逐条相关度。继续接入只能按工单取前 3 个结构化命中 ID，不能把随机联想区也喂给模型。
5. 按完整编号查询走另一条路径，不能代替自动召回和隐藏验证；本测试隐藏验证使用普通关键词。

## 可复跑

使用忽略目录内或仓库外的**未修改**上游固定提交和独立环境，不需要密钥。在本仓库根目录执行（按自己的依赖位置填路径）：

```powershell
# upstream 为上述固定提交的完整 Git 检出，ombre-env 为隔离环境。
# 先用该环境安装上游 requirements（3.12 可用其 lock）。
$env:OMBRE_SOURCE = (Resolve-Path .local-tmp/upstream).Path
$env:OMBRE_PYTHON = (Resolve-Path .local-tmp/ombre-env/Scripts/python.exe).Path
$env:PYTHONUTF8 = '1'
& $env:OMBRE_PYTHON tests/test_ombre_compatibility.py
```

当前预期：打印每个固定样例及 Recall@3: 2/11，最后因未达标返回失败。没有设置两个 OMBRE 环境变量时，普通离线套件明确跳过外部依赖检查，不代表召回通过。

## 后续边界

工单要求：“不达标就停下在 PR 里说明，由 lan 决定要不要加一个便宜的向量服务（计入预算）”。当前未接入或购买服务，未改聊天主程序、人设、预算或安装入口。每 4 轮后台补记、重试状态、角色记忆隔离、system 注入、/memory 和 /forget 的产品接入仍未实现。

请先评审证据，再决定评估计入预算的向量服务或修订无向量方案，同时明确情感检索入口。不要将普通 CI 绿色解释成工单 005 完成；现在不让 lan 花时间做 8 轮聊天验收。
