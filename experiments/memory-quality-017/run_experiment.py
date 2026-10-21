"""Run the isolated 017 experiment through the native VEIL/Ombre and Serein paths.

All provider traffic uses the loopback budget proxy. This runner never reads
questions.jsonl until both systems have saved their memories.
"""
import json
import argparse
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from decimal import Decimal
from http.server import ThreadingHTTPServer
import threading

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUN = ROOT / ".local-tmp" / "experiment-017" / "runtime"
SEREIN_ROOT = ROOT / ".local-tmp" / "serein-017"
SEREIN_LIBS = ROOT / ".local-tmp" / "experiment-017" / "serein-libs"
PROXY_HOST = "127.0.0.1"


def require_isolation(path):
    path = Path(path).resolve()
    allowed = RUN.resolve()
    if path != allowed and not path.is_relative_to(allowed):
        raise RuntimeError("experiment path escaped the isolated runtime root")
    return path


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def add_serein_import_paths():
    # The import shim is installed outside the checkout's own src tree.
    # Save-path callers need deployment helpers before setup_serein runs.
    for path in (SEREIN_LIBS, SEREIN_ROOT / "src"):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


class Proxy:
    def __init__(self, ledger_name="meter.jsonl"):
        from dotenv import dotenv_values
        from budget_proxy import Meter, make_handler

        env = dotenv_values(ROOT / ".env", interpolate=False, encoding="utf-8-sig")
        keys = {"deepseek": env.get("DEEPSEEK_API_KEY", ""),
                "siliconflow": env.get("EMBED_API_KEY", "")}
        missing = [name for name, value in keys.items() if not value or not value.strip()]
        if missing:
            raise RuntimeError("missing local provider key(s): " + ", ".join(missing))
        ledger = require_isolation(RUN / ledger_name)
        self.meter = Meter(ledger)
        self.server = ThreadingHTTPServer((PROXY_HOST, 0), make_handler(self.meter, keys))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://{PROXY_HOST}:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.meter.close()


def provider_snapshot(meter):
    return {"paid_calls": meter.paid_requests, "free_model_calls": meter.free_requests,
            "committed_cny": str(meter.committed), "reserved_cny": str(meter.pending)}


def setup_serein(proxy, character):
    add_serein_import_paths()
    from fastapi.testclient import TestClient
    from serein.api.http import create_app
    from serein.bootstrap import initialize
    from serein.config import Settings
    from serein.configured_models import prepare_selected
    from serein.core.store import Store as SereinStore
    from serein.deployment import save_settings
    from serein.core.reader import Reader

    home = require_isolation(RUN / "serein" / character)
    home.mkdir(parents=True, exist_ok=True)
    database = home / "memory.sqlite3"
    settings = Settings(database, home / "index.sqlite3", writable=True)
    initialize(settings)
    with SereinStore(database, read_only=True) as store:
        imported_count = store.conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
    local_proxy = "proxy-dummy"
    models = [
        {"id": "answer", "label": "deepseek-flash", "model": "deepseek-flash",
         "base_url": proxy.base + "/serein_answer/deepseek/v1", "api_key": local_proxy,
         "protocol": "openai", "reasoning_content_compat": "off"},
        {"id": "track-router", "label": "deepseek-flash", "model": "deepseek-flash",
         "base_url": proxy.base + "/serein_track_router/deepseek/v1", "api_key": local_proxy,
         "protocol": "openai", "reasoning_content_compat": "off"},
        {"id": "event-curator", "label": "deepseek-flash", "model": "deepseek-flash",
         "base_url": proxy.base + "/serein_event_curator/deepseek/v1", "api_key": local_proxy,
         "protocol": "openai", "reasoning_content_compat": "off"},
        {"id": "event-writer", "label": "deepseek-flash", "model": "deepseek-flash",
         "base_url": proxy.base + "/serein_event_writer/deepseek/v1", "api_key": local_proxy,
         "protocol": "openai", "reasoning_content_compat": "off"},
        {"id": "embedding", "label": "BAAI/bge-m3", "model": "BAAI/bge-m3",
         "base_url": proxy.base + "/serein_index/siliconflow/v1", "api_key": local_proxy,
         "protocol": "openai", "dimension": 1024},
        {"id": "reranker", "label": "BAAI/bge-reranker-v2-m3", "model": "BAAI/bge-reranker-v2-m3",
         "base_url": proxy.base + "/serein_index/siliconflow/v1", "api_key": local_proxy,
         "protocol": "openai"},
    ]
    assignments = {"chat": "answer", "embedding": "embedding", "reranker": "reranker",
                   "track_router": "track-router", "event_curator": "event-curator",
                   "event_writer": "event-writer"}
    save_settings(database, {"models": models, "assignments": assignments,
        "identity": {"user_name": "User", "ai_name": "Experiment"},
        "upstream": {"memory_enabled": False, "operit_enabled": False},
        "pipeline": {"auto_enabled": False, "execution_mode": "api",
                     "event_writer_concurrency": 1}})
    # Semantic recall preparation uses only the explicitly allowlisted free BGE endpoint.
    prepared = prepare_selected(settings)
    app = create_app(settings, token="experiment-017-local", live=True)
    client = TestClient(app, headers={"Authorization": "Bearer experiment-017-local"})
    client.__enter__()
    return {"home": home, "database": database, "settings": settings,
            "client": client, "prepared": prepared, "Reader": Reader,
            "SereinStore": SereinStore, "imported_count": imported_count}


def wait_import(client, upload_id, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listing = client.get("/v1/imports").json()
        item = next((entry for entry in listing["items"] if entry["id"] == upload_id), None)
        if item and item["status"] == "completed":
            return item
        task = item.get("task") if item else None
        if task and task.get("status") in {"failed", "blocked", "paused", "needs_repair"}:
            raise RuntimeError("Serein import task stopped: " + str(task.get("status")))
        time.sleep(0.5)
    raise TimeoutError("Serein import processing timed out")


def wait_pipeline(client, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/v1/pipeline/status").json()
        state = status.get("status")
        if state in {"blocked", "paused", "needs_repair", "awaiting_agent", "failed", "retry_wait"}:
            raise RuntimeError("Serein Event pipeline stopped: " + str(state))
        if state == "completed" and status.get("stage") in {
                "current", "settled_today", "waiting_settlement_window", "auto_paused"}:
            return status
        time.sleep(1)
    raise TimeoutError("Serein Event pipeline timed out")


def save_serein(proxy, character):
    add_serein_import_paths()
    from serein.deployment import save_settings
    runtime = setup_serein(proxy, character)
    client = runtime["client"]
    receipt = None
    if runtime["imported_count"]:
        imported = {"total": runtime["imported_count"], "processed": runtime["imported_count"], "failed": 0}
        receipt = client.post("/v1/pipeline/next", json={"include_recent": False})
        receipt.raise_for_status()
    else:
        destination = ROOT / ".local-tmp" / "experiment-017" / "serein-imports" / f"{character}.json"
        content = destination.read_text(encoding="utf-8")
        preview = client.post("/v1/imports/preview", json={"filename": destination.name,
            "content": content, "mode": "conversation", "tagging": False})
        preview.raise_for_status()
        upload_id = preview.json()["id"]
        client.post(f"/v1/imports/{upload_id}/continue", json={}).raise_for_status()
        imported = wait_import(client, upload_id)
        if imported["total"] != imported["processed"] or imported["failed"]:
            raise RuntimeError("Serein original-message import was incomplete")
        receipt = client.post(f"/v1/imports/{upload_id}/summarize", json={})
        receipt.raise_for_status()
    pipeline = wait_pipeline(client)
    # No scheduled import or background pipeline may remain active at query time.
    with runtime["SereinStore"](runtime["database"], read_only=True) as store:
        kinds = {row["kind"]: row["n"] for row in store.conn.execute(
            "SELECT kind,COUNT(*) n FROM documents WHERE lifecycle!='deleted' GROUP BY kind")}
    if not kinds.get("event"):
        raise RuntimeError("Serein produced no canonical Events from the imported chat")
    from serein.configured_models import prepare_selected
    runtime["prepared_after_events"] = prepare_selected(runtime["settings"])
    # Verify actual recall readiness before enabling it in the chat gateway.
    from serein.configured_models import memory_ready
    if not memory_ready(runtime["settings"]):
        raise RuntimeError("Serein semantic recall is not ready after preparation")
    save_settings(runtime["database"], {"upstream": {"memory_enabled": True}})
    runtime.update({"memories": [], "import": {"messages": imported["total"],
        "pipeline_status": pipeline.get("status"), "kinds": kinds,
        "event_pipeline_receipt": receipt.json() if receipt is not None else None,
        "resumed_partial_import": bool(runtime["imported_count"])}})
    with runtime["Reader"](runtime["database"]) as reader:
        for row in reader.store.conn.execute("SELECT id,kind FROM documents WHERE kind IN ('event','scene') AND lifecycle!='deleted' ORDER BY created_at,id"):
            obj = reader.read(row["id"], with_evidence=True)
            if obj.get("readable"):
                runtime["memories"].append({"id": row["id"], "kind": row["kind"],
                    "title": obj["document"]["title"], "content": obj["document"]["body_md"],
                    "evidence": obj.get("evidence", [])})
    return runtime


def query_serein(runtime, question, question_id):
    response = runtime["client"].post("/v1/chat/completions", json={
        "messages":[
            {"role":"system","content":"只根据已召回的长期记忆回答；没有依据时明确说不知道，不要补充猜测。"},
            {"role":"user","content":question}],
        "serein":{"memory":True,"window_id":"exp017-eval-" + question_id},
        "stream":False, "max_tokens":800})
    response.raise_for_status()
    body = response.json()
    selected = json.loads(response.headers.get("X-Serein-Selected-Ids", "[]"))
    selected_items = [item for item in runtime["memories"] if item["id"] in set(selected)]
    return {"answer": body["choices"][0]["message"].get("content", ""),
            "selected_ids": selected, "selected_memories": selected_items,
            "observation_id": response.headers.get("X-Serein-Observation-Id", ""),
            "usage": body.get("usage", {})}


def model_call(client, model, messages, *, json_output=False, max_tokens=1200):
    options = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "stream": False}
    if json_output:
        options["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**options)
    usage = response.usage
    if usage is None:
        raise RuntimeError("model response did not include usage")
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(usage, "prompt_cache_hit_tokens", None)
    if cached is None:
        cached = getattr(details, "cached_tokens", 0) or 0
    choice = response.choices[0]
    return {"text": choice.message.content or "", "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens, "cached_tokens": cached,
            "finish_reason": choice.finish_reason}


def ombre_context(proxy, character, transcripts=None):
    sys.path.insert(0, str(ROOT))
    from memory_agent import MCP, Memory
    from ombre_resident import start_or_connect
    from veil_agent import Budget, Character, Store
    from openai import OpenAI

    home = require_isolation(RUN / "ombre" / character)
    vault = require_isolation(home / "data" / character / "memory")
    private = vault / "_agent"
    private.mkdir(parents=True, exist_ok=True)
    store = Store(home / "data")
    model_url = proxy.base + "/ombre_extract/deepseek/v1"
    internal_url = proxy.base + "/ombre_internal/deepseek/v1"
    embed_url = proxy.base + "/ombre_embedding/siliconflow/v1"
    with socket.socket() as port_socket:
        port_socket.bind((PROXY_HOST, 0))
        ombre_port = port_socket.getsockname()[1]
    provider = {"model": "deepseek-flash", "base_url": internal_url,
                "api_key_env": "LOCAL_PROXY_DUMMY", "input_cny_per_million": Decimal("2"),
                "cached_input_cny_per_million": Decimal("0.04"),
                "output_cny_per_million": Decimal("8"), "max_input_tokens": 12000}
    memory_cfg = {"model": "BAAI/bge-m3", "base_url": embed_url,
                  "input_cny_per_million": Decimal("0"), "dimensions": 1024,
                  "mode": "extract", "extract_max_output_tokens": 1200,
                  "ombre_port": ombre_port}
    cfg = Character(character, character.title(),
                    "只按当前消息和被提供的记忆回答；没有依据时明确说不确定。",
                    20, 1200, provider, "experiment", memory_cfg, None)
    try:
        port, token, resident = start_or_connect(ROOT, cfg, data_root=home / "data")
    except Exception as exc:
        raise RuntimeError("isolated Ombre HTTP server failed to start; check the pinned local runtime") from exc
    try:
        backend = MCP(f"http://{PROXY_HOST}:{port}/mcp", token)
    except Exception as exc:
        resident.close()
        raise RuntimeError("isolated Ombre HTTP MCP connection failed; no experiment tool call was submitted") from exc
    client = OpenAI(api_key="proxy-dummy", base_url=model_url, max_retries=0, timeout=120)
    answer_client = OpenAI(api_key="proxy-dummy",
        base_url=proxy.base + "/ombre_answer/deepseek/v1", max_retries=0, timeout=120)

    def extract_model(messages, *, json_output=False, max_tokens=None, detail=False,
                      thinking=None, **_ignored):
        result = model_call(client, "deepseek-flash", messages,
                            json_output=json_output, max_tokens=max_tokens or 1200)
        base = (result["text"], result["prompt_tokens"], result["completion_tokens"], result["cached_tokens"])
        return base + (result["finish_reason"],) if detail else base

    total_turns = 0
    if transcripts is not None:
        if store.read(f"{character}/chat.jsonl"):
            raise RuntimeError("Ombre isolated chat already exists; rerun with --fresh")
        turns = sorted((row for row in transcripts if row["character"] == character), key=lambda row: row["seq"])
        for row in turns:
            store.append(f"{character}/chat.jsonl", {"role": row["speaker"], "content": row["text"],
                                                       "request_id": f"exp017-{character}-{row['seq']}"})
        total_turns = sum(row["character"] == character and row["speaker"] == "user" for row in transcripts)
        store.append(f"{character}/memory/_agent/state.jsonl", {"state": {
            "cursor": 0, "pending": [], "entries": {}, "sources": {}}})
    budget_store = Store(home / "budget")
    budget = Budget(budget_store, daily=Decimal("6"), monthly=Decimal("6"))
    records = []
    memory = None
    try:
        memory = Memory(cfg, store, budget, extract_model, backend)
        if transcripts is not None:
            while memory.state["cursor"] + 4 <= total_turns:
                memory.supplement()
                records.append({"cursor": memory.state["cursor"], "entries": len(memory.state["entries"])})
        saved = []
        for key, value in memory.state["entries"].items():
            title, body = memory.bucket_details(key, refresh=True)
            saved.append({"id": key, "title": title or value.get("title", ""),
                          "content": body or value.get("text", ""),
                          "hidden": bool(value.get("hidden"))})
        return {"home": home, "store": store, "cfg": cfg, "budget": budget,
                "memories": saved, "batches": records, "memory": memory,
                "backend": backend, "client": client, "answer_client": answer_client,
                "resident": resident, "closed": False}
    except BaseException:
        if memory is not None:
            memory.__exit__(None, None, None)
        backend.close()
        resident.close()
        answer_client.close()
        raise


def close_ombre(runtime):
    if runtime.get("closed"):
        return
    runtime["memory"].__exit__(None, None, None)
    runtime["backend"].close()
    runtime["resident"].close()
    runtime["client"].close()
    runtime["answer_client"].close()
    runtime["closed"] = True


def query_ombre(runtime, question):
    context = runtime["memory"].context(question)
    answer = model_call(runtime["answer_client"], "deepseek-flash", [
        {"role": "system", "content": "只根据给出的长期记忆回答；没有依据时明确说不知道，不要补充猜测。"},
        {"role": "system", "content": "召回到的记忆：\n" + "\n".join(item["content"] for item in context)},
        {"role": "user", "content": question}], max_tokens=800)
    return {"answer": answer["text"], "retrieved_context": context,
            "usage": {key: answer[key] for key in ("prompt_tokens", "completion_tokens", "cached_tokens")}}


def ledger_summary(path, line_range=None):
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if line_range is not None:
        start, end = line_range
        lines = lines[start:end]
    rows = [json.loads(line) for line in lines]
    reservations = {row["request_id"]: row for row in rows if row["action"] == "reserve"}
    totals = {}
    for row in rows:
        if row["action"] not in {"reserve", "settle"}:
            continue
        reservation = reservations.get(row["request_id"], row)
        group = (reservation["system"], reservation["provider"], reservation["model"])
        item = totals.setdefault(group, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
            "known_input_calls": 0, "known_output_calls": 0, "amount_cny": Decimal("0"),
            "request_bytes": 0})
        if row["action"] == "reserve":
            item["calls"] += 1
            item["request_bytes"] += int(row.get("request_body_bytes", 0))
        else:
            if isinstance(row.get("prompt_tokens"), int):
                item["input_tokens"] += row["prompt_tokens"]
                item["known_input_calls"] += 1
            if isinstance(row.get("completion_tokens"), int):
                item["output_tokens"] += row["completion_tokens"]
                item["known_output_calls"] += 1
            item["amount_cny"] += Decimal(row.get("amount_cny", "0"))
    return [{"stage": key[0], "provider": key[1], "model": key[2],
             **{name: str(value) if isinstance(value, Decimal) else value
                for name, value in values.items()}}
            for key, values in sorted(totals.items())]


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    if not (SEREIN_ROOT / "src" / "serein").is_dir():
        raise SystemExit("pinned Serein checkout is missing")
    transcripts = read_jsonl(HERE / "transcripts.jsonl")
    for row in transcripts:
        if row["character"] not in {"peter", "ghost"} or row["speaker"] not in {"user", "assistant"}:
            raise SystemExit("unexpected transcript role")
    sys.path.insert(0, str(HERE))
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / ".env", interpolate=False, encoding="utf-8-sig")
    if not values.get("DEEPSEEK_API_KEY") or not values.get("EMBED_API_KEY"):
        raise SystemExit("local model keys are missing")
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-serein", action="store_true",
                        help="configure an isolated Serein instance and check only its free embedding route")
    parser.add_argument("--preflight-ombre", action="store_true",
                        help="start the isolated native Ombre MCP with empty vaults; no provider request")
    parser.add_argument("--fresh", action="store_true",
                        help="reset only isolated memory/result data; keep the cumulative budget ledger")
    options = parser.parse_args()
    preflight = options.preflight_serein or options.preflight_ombre
    if options.fresh and preflight:
        raise SystemExit("--fresh cannot be combined with a preflight")
    if options.fresh:
        for name in ("serein", "ombre"):
            target = require_isolation(RUN / name)
            if target.exists():
                shutil.rmtree(target)
        for name in ("results.json", "lifecycle.json"):
            target = require_isolation(RUN / name)
            if target.is_file():
                target.unlink()
    from budget_proxy import ledger_line_count
    proxy = Proxy("preflight/meter.jsonl" if preflight else "meter.jsonl")
    main_ledger_start = ledger_line_count(RUN / "meter.jsonl") if not preflight else None
    if options.preflight_serein:
        runtimes = []
        try:
            for character in ("preflight_peter", "preflight_ghost"):
                runtime = setup_serein(proxy, character)
                runtimes.append(runtime)
                print(json.dumps({"phase": "serein_preflight_complete", "character": character,
                    "model_preparation": runtime["prepared"], **provider_snapshot(proxy.meter)}, ensure_ascii=True))
        finally:
            for runtime in runtimes:
                runtime["client"].__exit__(None, None, None)
            proxy.close()
        return
    if options.preflight_ombre:
        runtimes = []
        try:
            for character in ("preflight_ombre_peter", "preflight_ombre_ghost"):
                runtime = ombre_context(proxy, character)
                runtimes.append(runtime)
                close_ombre(runtime)
                print(json.dumps({"phase": "ombre_mcp_preflight_complete", "character": character,
                    **provider_snapshot(proxy.meter)}, ensure_ascii=True))
            if proxy.meter.paid_requests:
                raise RuntimeError("Ombre startup unexpectedly sent a paid call")
        finally:
            for runtime in runtimes:
                if not runtime["log"].closed:
                    close_ombre(runtime)
            proxy.close()
        return
    serein, ombre = {}, {}
    characters = ("peter", "ghost")
    transcript_map = {name: [row for row in transcripts if row["character"] == name] for name in characters}
    try:
        for character in characters:
            serein[character] = save_serein(proxy, character)
            print(json.dumps({"phase": "serein_saved", "character": character,
                "objects": len(serein[character]["memories"]),
                **provider_snapshot(proxy.meter)}, ensure_ascii=True))
        for character in characters:
            initial = ombre_context(proxy, character, transcript_map[character])
            try:
                saved, batches = initial["memories"], initial["batches"]
            finally:
                close_ombre(initial)
            # Reopen the isolated role vault to verify that saved memories survive restart.
            ombre[character] = ombre_context(proxy, character)
            ombre[character]["memories"] = saved
            ombre[character]["batches"] = batches
            print(json.dumps({"phase": "ombre_reopened", "character": character,
                "objects": len(saved), **provider_snapshot(proxy.meter)}, ensure_ascii=True))

        # Blind questions are read only after both systems finish autonomous saving.
        questions = read_jsonl(HERE / "questions.jsonl")
        observations = []
        for question in questions:
            character = question["character"]
            observations.append({"id": question["id"], "character": character,
                "question": question["question"],
                "serein": query_serein(serein[character], question["question"], question["id"]),
                "ombre": query_ombre(ombre[character], question["question"]),
                "gold": {"facts": question["facts"], "evidence_seq": question["evidence_seq"]}})
            print(json.dumps({"phase": "question_complete", "id": question["id"],
                **provider_snapshot(proxy.meter)}, ensure_ascii=True))
        main_ledger_range = [main_ledger_start, ledger_line_count(RUN / "meter.jsonl")]
        result = {"fixture_version": 1, "transcripts": transcripts,
            "serein": {name: {"import": item["import"], "memories": item["memories"]}
                       for name, item in serein.items()},
            "ombre": {name: {"batches": item["batches"], "memories": item["memories"]}
                      for name, item in ombre.items()},
            "questions": observations, "ledger_range": main_ledger_range,
            "cost_by_stage": ledger_summary(RUN / "meter.jsonl", main_ledger_range),
            "budget": provider_snapshot(proxy.meter)}
        output_path = require_isolation(RUN / "results.json")
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"phase": "complete", "result_file": str(output_path),
            "budget": result["budget"]}, ensure_ascii=True))
    finally:
        for runtime in serein.values():
            runtime["client"].__exit__(None, None, None)
        for runtime in ombre.values():
            close_ombre(runtime)
        proxy.close()


if __name__ == "__main__":
    main()


