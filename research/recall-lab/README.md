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
        --embedding-rate-cny-per-million 0 \
        --cache-dir ../.local-tmp/s4/cache \
        --budget-ledger ../.local-tmp/s4/budget-ledger.jsonl \
        --detail-dir ../.local-tmp/s4/runs \
        --tier all

- `--tier 0` 只跑零付费离线实验；`--tier 1` 检索/判官实测；`--tier 2` 回复体验/上下文成本。
- `--embedding-rate-cny-per-million` 是**必填**的显式单价声明（0.0 仅当 embedding 端点为免费/已含订阅价）；未声明价格一律拒绝，避免账本少记花费。每次 embedding 调用前按 token 上界 reserve、调用后 settle。
- `--skip-paid` 只跑 Tier 0 且 embedding 为 **cache-only**：缓存未命中直接拒绝，不会发起任何 provider 请求；被跳过的实验在 coverage 中记为 `not_run`。`--fake-embedder` 只用本地假向量验证 harness（结果不能当真实基线）。
- `--experiments AB-R1a,AB-R3a` 可单跑指定实验（ID 大小写无关）；已有结果会被读取用于依赖链（断点续跑）。
- env 文件只读，需要 `EMBED_API_KEY`（Tier 0/1 向量）与 `DEEPSEEK_API_KEY`（Tier 1/2 判官/生成）；`--skip-paid` 不需要任何 key。

## 目录

    recall_lab/core.py       数据快照（只读 brain.db / eval-v2 / chat / learned-context）、时序审计、预算台账、指标
    recall_lab/retrieval.py  检索基线复刻 + 单变量变体（K/停用词/融合/importance/last_active/情感/
                             related_to/别名 Aho-Corasick/story 索引/生产 breath/全库分层轮换池/wake 门控）
    recall_lab/rerank.py     SiliconFlow /v1/rerank 客户端（BAAI/bge-reranker-v2-m3 免费、Qwen3-Reranker-0.6B）
    recall_lab/s2_verify.py  AB-A2/A3 隔离临时副本运行最新 S2 EventMemory 与 LearnedContext 真实写入验证
    recall_lab/judge.py      判官卡片（300/500）、prompt（保守/放宽）、LLM 调用、阈值门控
    recall_lab/tier0.py      11 个 Tier 0 实验 runner
    recall_lab/tier12.py     Tier 1/2 runner（Reranker、判官臂、真实上下文与工具调用、认知巩固、组合验证）
    recall_lab/tier2_real.py Tier 2 真实模型生成、工具调用、前瞻巩固与 8 维匿名 AI 辅助盲评（生成本机 Lan 对照包）
    recall_lab/comb_e2e.py   AB-COMB 端到端 4 路 Rerank+Judge 联动、多机制回复对照与离线时间衰减保护可复现入口
    recall_lab/runtime.py    Context（query 向量缓存、评估、结果 I/O）、快照指纹
    recall_lab/cli.py        命令行入口、依赖链、断点续跑、覆盖统计
    tests/                   合成数据单测（40 项，无网络、无真实数据）
    schema/                  eval-v2 / 预算台账 / 实验结果 JSON Schema
    results/                 已提交的聚合结果（24/24 completed + coverage.json + run-manifest.json + budget-summary.json）

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
3. `python scripts/p6_s1_baseline.py --out .local-baseline-1.json` 复跑 Baseline-1 门禁，确认 20/20。
4. 单测：`python -m unittest discover -s recall-lab/tests -v`（当前 40 项，含并发账本、cache-only、缺 usage 保守结算、门控口径、生产 breath/分层轮换/wake 门控、时序审计、Reranker 价格拦截、S2 隔离写入验证与 `comb_e2e` 衰减保护回归）。
5. 复跑 `AB-COMB` 端到端补测与离线衰减保护（命中本地缓存时新增费用 ¥0）：
   `python -m recall_lab.comb_e2e --db <isolated/brain.db> --eval <eval-v2.jsonl> --learned-context <learned-context.json> --rerank-detail <runs/ab_r2_Qwen_Qwen3-Reranker-0_6B.jsonl> --cache-dir <cache> --results-dir results --detail-dir <runs> --budget-ledger <budget-ledger.jsonl>`（加 `--offline-only` 可零费用仅跑衰减保护检查）。

## 已知边界

- eval-v2 为 AI 辅助标注（labeler=ai-assisted, human_confirmed=false），不是 Lan 人工金标准；
  所有 Tier 2 结论都是 AI 辅助且未经人工确认。
- 本 harness 不加载完整生产请求（人设正文、全量工具 schema、真实 chat 历史），Tier 2 的
  token/缓存结论是结构侧/微探针证据，需 S7 在真实生产请求上复测。
- 原「豆辞典」数据源在仓库中不存在，AB-R4c 用从记忆标题/标签/关键词自动抽取的别名字典代替，
  已在结果中记录该证据缺口。
