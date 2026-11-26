# recall-lab — P6-S4 隔离消融实验设施

P6-S4（统一消融实验执行与候选机制决策）的隔离实验环境。目标是在**不修改生产源码、不写入正式记忆、
不启停正式进程**的前提下，用独立标注集回放真实数据，对 P6-4 第五章的 24 个实验 ID 做单变量对照，
留下可复现、可审计、预算硬控的台账。

## 边界与隐私

- 所有 brain.db 均以 SQLite 只读 URI（`mode=ro`）打开，只读。
- 真实数据（eval-v2、隔离快照、chat、learned-context）**不进 git**：通过 `--eval/--db/...`
  参数或 `.local-tmp/` 路径提供；本目录的 `results/` 只保存聚合指标、哈希与状态。
- 不读取、不写入正式运行目录；不向正式 Peter 发消息；密钥只从只读 env 文件或环境变量读取，
  不落盘、不进日志、不进结果。
- 合成单测不依赖真实数据与网络：`python -m unittest discover -s recall-lab/tests -v`。

## 快速开始

    cd recall-lab
    python -m recall_lab.cli --list          # 24 个实验 ID
    python -m recall_lab.cli \
        --eval ../.local-tmp/s4/eval-v2.jsonl \
        --db ../.local-tmp/s4/isolated/brain.db \
        --chat ../.local-tmp/s4/isolated/chat.jsonl \
        --learned-context ../.local-tmp/s4/extra/learned-context.json \
        --versions ../.local-tmp/s4/extra/learned-context.versions.jsonl \
        --context-log ../.local-tmp/s4/extra/context-log.jsonl \
        --recall-diagnostics ../.local-tmp/s4/extra/recall-diagnostics.jsonl \
        --capabilities ../.local-tmp/s4/extra/capabilities.json \
        --env-file ../../../veil-agent/.env \
        --cache-dir ../.local-tmp/s4/cache \
        --budget-ledger ../.local-tmp/s4/budget-ledger.jsonl \
        --detail-dir ../.local-tmp/s4/runs \
        --tier all

- `--tier 0` 只跑零付费离线实验；`--tier 1` 检索/判官实测；`--tier 2` 回复体验/上下文成本。
- `--skip-paid` 只跑 Tier 0；`--fake-embedder` 用本地假向量只验证 harness（结果不能当真实基线）。
- `--experiments AB-R1a,AB-R3a` 可单跑指定实验；已有结果会被读取用于依赖链（断点续跑）。
- env 文件只读，需要 `EMBED_API_KEY`（Tier 0/1 向量）与 `DEEPSEEK_API_KEY`（Tier 1/2 判官/生成）。

## 目录

    recall_lab/core.py       数据快照（只读 brain.db / eval-v2 / chat / learned-context）、预算台账、指标
    recall_lab/retrieval.py  检索基线复刻 + 单变量变体（K/停用词/融合/importance/last_active/情感/
                             related_to/别名 Aho-Corasick/story 索引/分层联想池）
    recall_lab/judge.py      判官卡片（300/500）、prompt（保守/放宽）、LLM 调用、阈值门控
    recall_lab/tier0.py      11 个 Tier 0 实验 runner
    recall_lab/tier12.py     Tier 1/2 runner（预算台账、判官臂、缓存微探针、token 结构测量、组合）
    recall_lab/runtime.py    Context（query 向量缓存、评估、结果 I/O）、快照指纹
    recall_lab/cli.py        命令行入口、依赖链、断点续跑、覆盖统计
    tests/                   合成数据单测（无网络、无真实数据）
    schema/                  eval-v2 / 预算台账 / 实验结果 JSON Schema
    results/                 已提交的聚合结果（每个实验一个 JSON + coverage.json + run-manifest.json）

## 预算与台账

`BudgetLedger` 是 append-only JSONL：调用前 `reserve`（按最大 token 上界），调用后 `settle`（按
provider 回报 usage）；失败调用按预留上界保守结算。硬上限：Tier 0 = ¥0、Tier 1 ≤ ¥6、Tier 2 ≤ ¥10、
总 ≤ ¥20。无法确认价格上界的付费调用一律拒绝（例如无 rerank 供应商时的 AB-R2）。

- Embedding 预计算（73 条 query + 76 条 story）按 provider 配置价 ¥0 记账，但**仍记录 model、调用量
  与 token 数**；重跑时命中本地缓存，不重复付费。
- 判官/生成结果按 (model, messages, timeout) 哈希缓存，重跑只命中缓存，不重复付费。

## 指标口径

- `Recall@K` / `Precision@5` / `NDCG@8`：allow_empty=false 样本；期望 id 可为 memory id 或
  事件 id（V 系列事件 id 同时是 memory id）。
- `FPR`（误召回率）：allow_empty=true 样本中发生了注入即误报。Tier 0 无付费判官，统一用离线门控
  （向量余弦 ≥ 0.45 且最多 3 条，与 AB-R3b 阈值门控同口径）计算 `gated.false_positive_rate`；
  原始候选注入在判官前必然非空，不作为 Tier 0 决策信号。
- `跨事件覆盖率`：S3 样本中 top-K 命中 ≥2 个期望 id 的比例。
- `循环自证率`：AB-A2 中「无 Lan 原话/chat 证据却被晋升 confirmed」的比例，红线必须为 0。
- 检索延迟为排序耗时（不含已缓存的 embedding 与 DB 加载）；判官延迟为真实 provider 往返。

## 复现

1. 从 `.local-tmp/s4-prep`（或由 operator 自行准备的同结构目录）复制 eval-v2 与只读快照到
   `.local-tmp/s4/`（该目录被 `.gitignore` 忽略，不进 git）。
2. 按「快速开始」命令运行；每个实验写 `results/<实验>.json`，每样本明细只写 `--detail-dir`
   （gitignored）。
3. `python scripts/p6_s1_baseline.py --out .local-baseline-1.json` 复跑 Baseline-1 门禁，确认 18/18。
4. 单测：`python -m unittest discover -s recall-lab/tests -v`。

## 已知边界

- eval-v2 为 AI 辅助标注（labeler=ai-assisted, human_confirmed=false），不是 Lan 人工金标准；
  所有 Tier 2 结论都是 AI 辅助且未经人工确认。
- 本 harness 不加载完整生产请求（人设正文、全量工具 schema、真实 chat 历史），Tier 2 的
  token/缓存结论是结构侧/微探针证据，需 S7 在真实生产请求上复测。
- 原「豆辞典」数据源在仓库中不存在，AB-R4c 用从记忆标题/标签/关键词自动抽取的别名字典代替，
  已在结果中记录该证据缺口。
