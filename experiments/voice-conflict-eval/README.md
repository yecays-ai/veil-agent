# voice-conflict-eval

冲突场景的回归集。换模型、改人设、改 `peter.voice.md` 之后跑一遍，看会不会退化成客服腔。

## 跑法

```powershell
python experiments/voice-conflict-eval/run_eval.py --out out.md
```

- 读项目 `.env` 里的密钥；用 `data/<角色>/models.json` 里登记的接口，没登记就用内置 DeepSeek。
- 每题会先送人设（`persona/<角色>.md`）和本机 `persona/<角色>.voice.md`（存在才送）。
- 会真的调用模型并产生费用：默认 14 题 × 每个接口，一次约 ¥0.1–0.5，取决于模型。

## 判读

只问一句：**他这句话说完，你还想不想回。**

硬失败：问她要修复说明书（`what do you want me to do` / `what would be useful` / `tell me why` / `what happened`）、她说不想听解释就整段停下、复述她刚骂的那句。
