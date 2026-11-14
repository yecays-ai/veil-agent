# 工单 351 实验：给 Peter 看他自己说过的话

只在 Lan 电脑上跑；不改正式运行的 Peter、记忆或人设，随时可删。真实聊天、人设、候选句、回复和对比页都只写进 `--out` 指定的仓库外目录，脚本拒绝写进仓库。

`scenarios.json` 是 6 个虚构场景（闲聊、她心情不好、聊技术、他被质疑、玩笑、道别）。

```
python experiments/style-snippet-351/style_probe.py candidates --chat <只读的 chat.jsonl> --out <输出目录>
#   或：由人在本机读聊天后给出几段行号，切成来回片段做候选
python experiments/style-snippet-351/style_probe.py exchanges --chat <chat.jsonl> --ranges "120-125 300-303" --out <输出目录>
#   Lan 打开 <输出目录>/candidates.html，挑 20–30 句，报编号
python experiments/style-snippet-351/style_probe.py select --out <输出目录> --numbers "1 3 5-9"
python experiments/style-snippet-351/style_probe.py run --out <输出目录> --persona <人设> --profile <关于 Lan 的笔记> \
    --name Peter --user-name Lan --models <models.json> --env <测试副本 .env>
python experiments/style-snippet-351/style_probe.py page --out <输出目录> --models <models.json>
#   Lan 打开 compare.html，回复如 "1X 2Y 3一样 ..."
python experiments/style-snippet-351/style_probe.py reveal --out <输出目录> --answers "1X 2Y ..."
```

`models.json` 每项：`label`、`base_url`、`model`、`api_key_env`、三个价格（元/百万 token，`input_cny_per_million`、`cached_input_cny_per_million`、`output_cny_per_million`），可选 `extra_body`。

费用：每次调用前按「字符数当 token 数」的最坏情况预留，累计到 ¥2 就停；实际花费按接口返回用量记进 `<输出目录>/cost.jsonl`（不含正文）。`run` 中断后重跑只补没跑的格子。

片段的位置：人设 → 关于 Lan 的笔记 → 片段块 → 场景消息，和正式聊天的可缓存前缀同序；两种条件只差片段块。

自动检查：`python -m unittest tests.test_style_probe_351`（虚构数据、假模型，不联网）。
