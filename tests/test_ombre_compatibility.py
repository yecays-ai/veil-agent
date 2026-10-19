"""Offline prerequisite gate for ticket 005; never connects to a model.

Set OMBRE_SOURCE to the unmodified 115d831 checkout and OMBRE_PYTHON to
its Python executable. Run this file using a Python with the official mcp SDK.
The normal suite skips this optional external-dependency test.
"""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


# Synthetic source conversations and synthetic bilingual extractor output.
# The query cases are fixed before running retrieval, not generated from hits.
FACTS = [
    ("interview", "Job interview", "I have a job interview next Thursday.",
     "下周四有一场工作面试。There is a job interview next Thursday.",
     "面试 求职 找工作 interview job application Thursday", False),
    ("tea", "Tea preference", "我喜欢茉莉花茶，不喜欢咖啡。",
     "喜欢茉莉花茶，不喜欢咖啡。Likes jasmine tea and dislikes coffee.",
     "茉莉花茶 喝茶 饮料 jasmine tea drink coffee preference", False),
    ("cat", "Cat checkup", "My cat Luna needs a vet checkup on Saturday.",
     "猫咪 Luna 周六要去兽医那里体检。The cat Luna has a vet checkup on Saturday.",
     "猫 猫咪 宠物 体检 兽医 医院 cat pet vet checkup Saturday", False),
    ("walk", "Evening walks", "晚饭后去河边散步让我放松。",
     "晚饭后去河边散步能放松。Walking by the river after dinner is relaxing.",
     "散步 放松 河边 晚饭 walk stroll unwind river dinner", False),
    ("pottery", "Pottery class", "My pottery class starts next Sunday.",
     "陶艺课下周日开始。Pottery class starts next Sunday.",
     "陶艺 手工 课程 周日 pottery ceramics class Sunday hobby", False),
    ("birthday", "Sister birthday", "我妹妹的生日是十一月三日。",
     "妹妹的生日是十一月三日。Her younger sister's birthday is November third.",
     "妹妹 生日 家人 礼物 sister birthday November gift family", False),
    ("lonely", "Missing home", "Moving away from home has made me feel lonely.",
     "搬离家后感到孤独和想家。Moving away from home has brought loneliness and homesickness.",
     "搬家 孤独 想家 lonely loneliness homesick moving home", True),
    ("proud", "Finished thesis", "论文终于写完了，我今天真的很为自己骄傲。",
     "论文完成后为自己感到骄傲。Finishing the thesis brought a feeling of pride.",
     "论文 完成 骄傲 开心 thesis finished proud pride accomplishment", True),
]

CASES = [
    ("Chinese about English", "下周四的面试我有点紧张", "interview"),
    ("Chinese about English", "猫咪周六去兽医那里体检", "cat"),
    ("Chinese about English", "下周日的陶艺课", "pottery"),
    ("English about Chinese", "What tea do I like?", "tea"),
    ("English about Chinese", "Walking by the river after dinner", "walk"),
    ("English about Chinese", "When is my sister's birthday?", "birthday"),
    ("Paraphrase", "最近找工作，想练习一下求职", "interview"),
    ("Paraphrase", "I need to unwind with a stroll", "walk"),
    ("Paraphrase", "I want to buy a gift for my sister", "birthday"),
    ("Emotion", "最近搬家后还是很孤独很想家", "lonely"),
    ("Emotion", "I'm still proud of finishing my thesis", "proud"),
]


def result_text(result):
    text = "\n".join(item.text for item in result.content if hasattr(item, "text"))
    if result.isError:
        raise AssertionError(text)
    return text


def hit_ids(text):
    # Only machine-declared search matches count. Ombre also emits unrelated
    # random memories in a separate section; those are NOT retrieval successes.
    match = re.search(r"=== ombre:result-ids ===\s*```json\s*(.*?)\s*```", text, re.S)
    if not match:
        raise AssertionError("Missing upstream structured result IDs")
    return json.loads(match.group(1))["bucket_ids"][:3]


@unittest.skipUnless(os.environ.get("OMBRE_SOURCE") and os.environ.get("OMBRE_PYTHON"),
                     "optional: set OMBRE_SOURCE and OMBRE_PYTHON")
class OmbreCompatibility(unittest.TestCase):
    def test_offline_recall_gate(self):
        asyncio.run(self.exercise())

    async def exercise(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        source = Path(os.environ["OMBRE_SOURCE"]).resolve()
        child = str(Path(os.environ["OMBRE_PYTHON"]).resolve())
        subprocess.run([child, "-c", "import jieba, rank_bm25; import sys; print(sys.version)"],
                       check=True, capture_output=True, timeout=30)
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "memory"
            vault.mkdir()
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({
                "transport": "stdio", "buckets_dir": str(vault), "ai_name": "Test",
                "dehydration": {"api_key": ""},
                "embedding": {"enabled": False, "api_key": ""},
                "github": {"enabled": False}, "log_level": "ERROR",
            }), encoding="utf-8")
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                       OMBRE_CONFIG_PATH=str(config), OMBRE_VAULT_DIR=str(vault),
                       OMBRE_LOG_DIR=temp, OMBRE_HOOK_SKIP="1", OMBRE_TRANSPORT="stdio")
            params = StdioServerParameters(command=child,
                args=[str(source / "src/server.py")], env=env, cwd=temp)
            ids = {}
            with (Path(temp) / "stderr.log").open("w", encoding="utf-8") as log:
                async with asyncio.timeout(120):
                    async with stdio_client(params, errlog=log) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result_text(await session.call_tool("breath", {}))
                            for key, title, quote, body, keywords, feel in FACTS:
                                parent = ""
                                if feel:
                                    # Upstream requires an existing source bucket
                                    # for feel=True. Keep that source linguistically
                                    # distinct, so a source hit cannot count as an
                                    # emotional bucket hit.
                                    original = result_text(await session.call_tool("hold", {
                                        "content": quote, "title": "Source " + title,
                                        "source_content": "user: " + quote,
                                    }))
                                    parent = re.search(r"新建→([a-f0-9]{12})", original).group(1)
                                text = result_text(await session.call_tool("hold", {
                                    "title": title, "content": body + "\n检索词：" + keywords,
                                    "feel": feel, "quotes": [{"text": quote, "speaker": "user"}],
                                    "source_bucket": parent,
                                    # Ombre disallows test_data+feel. All fixtures
                                    # remain synthetic in this disposable vault.
                                    "source_content": "user: " + quote, "test_data": False,
                                }))
                                match = re.search(r"(?:新建|feel)→([\w-]+)", text)
                                self.assertIsNotNone(match, text)
                                ids[key] = match.group(1)
                    # New process, same temporary vault: verify persistence.
                    async with stdio_client(params, errlog=log) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            # First search starts upstream's background BM25 build.
                            # Wait for this tiny fixture index, then measure warm
                            # retrieval so cold start cannot explain missed hits.
                            result_text(await session.call_tool("breath_search", {
                                "query": "warmup", "mode": "automatic", "with_ids": True,
                                "max_results": 3}))
                            await asyncio.sleep(3)
                            results = []
                            for category, query, expected in CASES:
                                text = result_text(await session.call_tool("breath_search", {
                                    "query": query, "mode": "automatic", "with_ids": True,
                                    "max_results": 3,
                                }))
                                hits = hit_ids(text)
                                passed = ids[expected] in hits
                                results.append(passed)
                                print(json.dumps({"category": category, "query": query,
                                    "expected": expected, "hit": passed, "count": len(hits)},
                                    ensure_ascii=False), flush=True)
                            for query, expected in [("interview", "interview"),
                                                    ("jasmine tea", "tea"),
                                                    ("pottery", "pottery")]:
                                control = result_text(await session.call_tool("breath_search", {
                                    "query": query, "mode": "automatic", "with_ids": True,
                                    "max_results": 3}))
                                self.assertIn(ids[expected], hit_ids(control))
                            negative = result_text(await session.call_tool("breath_search", {
                                "query": "volcano submarine", "mode": "automatic",
                                "with_ids": True, "max_results": 3}))
                            self.assertEqual([], hit_ids(negative))
                            # Native reversible suppression, with a lexical query rather
                            # than direct ID lookup (which follows a different policy).
                            result_text(await session.call_tool("trace", {
                                "bucket_id": ids["tea"], "dont_surface": 1}))
                            hidden = result_text(await session.call_tool("breath_search", {
                                "query": "jasmine tea", "mode": "automatic",
                                "with_ids": True, "max_results": 3}))
                            self.assertNotIn(ids["tea"], hit_ids(hidden))
                            result_text(await session.call_tool("trace", {
                                "bucket_id": ids["tea"], "dont_surface": 0}))
                            restored = result_text(await session.call_tool("breath_search", {
                                "query": "jasmine tea", "mode": "automatic",
                                "with_ids": True, "max_results": 3}))
                            self.assertIn(ids["tea"], hit_ids(restored))
                            print(f"Recall@3: {sum(results)}/{len(results)}", flush=True)
        self.assertTrue(all(results), "Ticket 005 recall gate failed")


if __name__ == "__main__":
    unittest.main()
