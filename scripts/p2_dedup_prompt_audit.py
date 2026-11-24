"""P2 Event & Fragment Deduplication and Prompt Injection Audit.

Verifies:
1. Exact mapping of 99df0c513389 and Event V01.
2. Injected prompt text vs memory_refs citation (sole-source folding).
3. Rule enforcement under ticket #424:
   - Sole-source folding & resolution to authoritative story
   - Co-occurrence deduplication (same_event drops redundant candidates)
   - Shared-source non-borrowing (all shared sources stay outside unique ledger)
4. Sanitized output: no private memory/chat text committed.
5. Self-contained fixture fallback for clean checkouts/CI.
"""

import os
import sys
import json
import sqlite3
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import memory_agent
import veil_brain
import brain_writer
from memory_agent import Memory
from veil_agent import Budget, Store, load_character

def build_fixture_sandbox(sandbox_root):
    """Build a self-contained fixture in sandbox if live data is not present."""
    sandbox_data = sandbox_root / "data" / "peter"
    sandbox_data.mkdir(parents=True, exist_ok=True)
    (sandbox_root / "characters" / "peter").mkdir(parents=True, exist_ok=True)
    (sandbox_root / "characters" / "peter" / "character.toml").write_text("""
[id]
id = "peter"

[memory]
model = "fake-model"
input_cny_per_million = 0.5
output_cny_per_million = 1.0

[memory.brain]
shadow = false
write_authority = true
""", encoding='utf-8')
    
    # Initialize SQLite schema
    db_path = sandbox_data / "brain.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(veil_brain.SCHEMA)
    brain_writer.ensure_schema(conn)
    c = conn.cursor()
    
    # Insert V01 and 99df0c513389
    c.execute("INSERT INTO memories (id, title, body, type, importance) VALUES ('99df0c513389', 'Cactus fragment', 'Fragment text about cactus', 'dynamic', 5)")
    c.execute("INSERT INTO memories (id, title, body, type, importance) VALUES ('V01', 'First night', 'V01 authoritative story text about cactus and red bricks', 'event', 8)")
    c.execute("INSERT INTO events (event_id, memory_id, source_count) VALUES ('V01', 'V01', 1)")
    c.execute("INSERT INTO event_sources (event_id, memory_id) VALUES ('V01', '99df0c513389')")
    
    # Insert 9 shared sources
    for i in range(1, 10):
        mid = f"shared_{i:04d}"
        ev1 = f"EV_{i}A"
        ev2 = f"EV_{i}B"
        c.execute("INSERT INTO memories (id, title, body, type) VALUES (?, ?, ?, 'dynamic')", (mid, f"Shared {i}", f"Shared body {i}"))
        c.execute("INSERT INTO events (event_id, memory_id, source_count) VALUES (?, ?, 1)", (ev1, ev1))
        c.execute("INSERT INTO events (event_id, memory_id, source_count) VALUES (?, ?, 1)", (ev2, ev2))
        c.execute("INSERT INTO event_sources (event_id, memory_id) VALUES (?, ?)", (ev1, mid))
        c.execute("INSERT INTO event_sources (event_id, memory_id) VALUES (?, ?)", (ev2, mid))
    
    conn.commit()
    conn.close()
    
    # Write events.jsonl
    events_file = sandbox_data / "events.jsonl"
    with open(events_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "event_id": "V01",
            "title": "First night",
            "story": "V01 authoritative story text about cactus and red bricks",
            "story_source": "authoritative",
            "sources": ["99df0c513389"]
        }) + "\n")
        for i in range(1, 10):
            mid = f"shared_{i:04d}"
            f.write(json.dumps({"event_id": f"EV_{i}A", "title": f"Event {i}A", "story": f"Story {i}A", "story_source": "authoritative", "sources": [mid]}) + "\n")
            f.write(json.dumps({"event_id": f"EV_{i}B", "title": f"Event {i}B", "story": f"Story {i}B", "story_source": "authoritative", "sources": [mid]}) + "\n")

    return sandbox_data

def run_audit():
    print("=== [1. Inspect Database & Events Mapping in Isolated Sandbox] ===")
    
    temp_dir = tempfile.mkdtemp(prefix="veil_dedup_audit_")
    try:
        sandbox_root = Path(temp_dir)
        sandbox_data = sandbox_root / "data" / "peter"
        
        # Check candidate locations for live data template
        src_peter = None
        for candidate in [
            ROOT / "data" / "peter",
            ROOT.parent / "veil-agent" / "data" / "peter",
            ROOT.parent.parent / "veil-agent" / "data" / "peter",
        ]:
            if candidate.exists() and (candidate / "brain.db").exists():
                src_peter = candidate
                break
        
        if src_peter:
            print(f"Using live data template from {src_peter}")
            shutil.copytree(src_peter, sandbox_data, ignore=shutil.ignore_patterns('*lock*', '*.lock', '_agent'))
            shutil.copytree(ROOT / "characters", sandbox_root / "characters")
            for candidate in [
                ROOT / "persona",
                ROOT.parent / "veil-agent" / "persona",
                ROOT.parent.parent / "veil-agent" / "persona",
            ]:
                if candidate.exists() and (candidate / "peter.md").exists():
                    shutil.copytree(candidate, sandbox_root / "persona")
                    break
        else:
            print("Notice: live data/peter not present, creating self-contained fixture sandbox")
            sandbox_data = build_fixture_sandbox(sandbox_root)
            
        if not (sandbox_root / "persona" / "peter.md").exists():
            (sandbox_root / "persona").mkdir(parents=True, exist_ok=True)
            (sandbox_root / "persona" / "peter.md").write_text("# Peter\nFake persona for test.", encoding='utf-8')
            
        # Reset rotation in sandbox so no candidates are on cooldown
        rotation_file = sandbox_data / "recent-rotation.json"
        with open(rotation_file, "w", encoding="utf-8") as f:
            json.dump({}, f)

        results = {}
        db_path = sandbox_data / "brain.db"
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        
        # 99df0c513389 details
        c.execute("SELECT id, type, is_deleted, is_tombstone FROM memories WHERE id='99df0c513389'")
        row = c.fetchone()
        assert row is not None, "Memory 99df0c513389 not found in brain.db"
        results['memory_99df0c513389_verified'] = {
            'id': row[0], 'type': row[1], 'is_deleted': row[2], 'is_tombstone': row[3]
        }
        
        # Event V01 details
        c.execute("SELECT event_id, memory_id, source_count FROM events WHERE event_id='V01'")
        v01_row = c.fetchone()
        assert v01_row is not None, "Event V01 not found in brain.db"
        results['event_V01_verified'] = {
            'event_id': v01_row[0], 'projection_memory_id': v01_row[1], 'source_count': v01_row[2]
        }
        
        # Sources of V01
        c.execute("SELECT memory_id FROM event_sources WHERE event_id='V01'")
        v01_sources = [r[0] for r in c.fetchall()]
        assert '99df0c513389' in v01_sources, "99df0c513389 not in V01 event_sources"
        results['v01_sources_count'] = len(v01_sources)
        results['v01_contains_target_fragment'] = True
        conn.close()

        # Read runtime events ledger from events.jsonl
        events_jsonl_path = sandbox_data / "events.jsonl"
        v01_authoritative = False
        v01_story_len = 0
        if events_jsonl_path.exists():
            with open(events_jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    data = json.loads(line)
                    if data.get("event_id") == "V01" or data.get("id") == "V01":
                        v01_authoritative = (data.get("story_source") == "authoritative" or bool(data.get("story")))
                        v01_story_len = len(data.get("story") or "")
                        break
        results['v01_authoritative'] = v01_authoritative
        results['v01_story_len'] = v01_story_len
        assert v01_story_len > 0, "V01 story length is 0"

        print("\n=== [2. Test Deduplication & Prompt Injection in Memory] ===")
        cfg = load_character(sandbox_root, "peter")
        store = Store(sandbox_root / "data")
        budget = Budget(store)
        mock_backend = Mock()
        # Mock backend returns candidate block for 99df0c513389 with raw frontmatter
        mock_candidate = "[bucket_id:99df0c513389]\ntype: dynamic\n2026-10-19 14-58-40 Kills plants even cacti\nRaw fragment text about cactus"
        mock_backend.call.return_value = mock_candidate
        mock_backend.call_safe.return_value = mock_candidate
        
        mem = Memory(cfg, store, budget, Mock(), mock_backend)
        
        ledger = mem._event_ledger()
        unique = ledger.get('unique', {})
        owners = ledger.get('owners', {})
        stories = mem._event_stories()
        
        assert unique.get('99df0c513389') == 'V01', "99df0c513389 unique owner must be V01"
        assert 'V01' in owners.get('99df0c513389', set()), "V01 must be in owners of 99df0c513389"
        assert stories.get('V01', {}).get('authoritative') is True, "V01 must be authoritative"
        
        results['unique_owner_99df0c513389'] = unique.get('99df0c513389')
        results['owners_99df0c513389'] = list(owners.get('99df0c513389', []))

        # Query with fresh rotation state
        query = "关于仙人掌的种植经历，有什么要注意的？"
        ctx = mem.context(query)
        last_refs = getattr(mem, 'last_context_refs', [])
        
        results['cactus_query_refs'] = last_refs
        assert '99df0c513389' in last_refs, f"Expected 99df0c513389 in last_context_refs, got {last_refs}"
        
        # Verify prompt body text
        full_prompt = "\n".join(b.get('content', '') for b in ctx)
        story_text = stories.get('V01', {}).get('story', '')
        
        # 1. Authoritative story text IS injected
        has_story = story_text in full_prompt or (story_text[:50] in full_prompt if story_text else False)
        # 2. Raw unmerged markdown frontmatter is NOT injected
        has_raw_frontmatter = "type: dynamic" in full_prompt or "2026-10-19 14-58-40" in full_prompt
        
        print(f"V01 full story present in prompt: {has_story}")
        print(f"Raw fragment unmerged markdown present in prompt: {has_raw_frontmatter}")
        assert has_story, "Authoritative V01 story was not injected into prompt"
        assert not has_raw_frontmatter, "Raw fragment frontmatter was leaked into prompt"
        
        results['v01_story_in_prompt'] = has_story
        results['raw_fragment_in_prompt'] = has_raw_frontmatter

        # Scenario B: Co-occurrence of Event Projection and Fragment
        print("\n=== [3. Deduplication Unit Drill: Both V01 and 99df0c513389 in Candidates] ===")
        mem.rotation.last.clear()
        v01_proj_id = v01_row[1] if v01_row else "V01"
        sim_candidates_text = f"""[bucket_id:{v01_proj_id}]
Authoritative story for V01

[bucket_id:99df0c513389]
type: dynamic
Fragment text

=== ombre:result-ids ===
```json
{{"bucket_ids": ["{v01_proj_id}", "99df0c513389"]}}
```
"""
        mem.search_candidates = lambda q, asked: sim_candidates_text
        ctx_dedup = mem.context('test query', include_opening=False)
        dropped = mem.last_recall_debug.get('dropped', [])
        print(f"Kept refs: {mem.last_context_refs}")
        print(f"Dropped candidates: {dropped}")
        assert v01_proj_id in mem.last_context_refs, f"Expected {v01_proj_id} in kept keys"
        assert any(d['id'] == '99df0c513389' and d['reason'] == 'same_event' for d in dropped), "99df0c513389 should be dropped as same_event duplicate"
        results['co_occurrence_drops_redundant_fragment'] = True

        # Scenario C: Shared source owned by multiple events
        print("\n=== [4. Shared Source Drill] ===")
        multi_owned = {m_id: ev_ids for m_id, ev_ids in owners.items() if len(ev_ids) > 1}
        print(f"Memories owned by multiple events: {len(multi_owned)}")
        assert len(multi_owned) >= 9, f"Expected at least 9 multi-owned memories, found {len(multi_owned)}"
        
        # Rule check: EVERY shared source must NOT be in unique ledger
        all_shared_not_in_unique = all(m_id not in unique for m_id in multi_owned)
        assert all_shared_not_in_unique, "Some shared source was incorrectly placed in unique ledger!"
        
        results['multi_owned_count'] = len(multi_owned)
        results['all_shared_sources_outside_unique'] = all_shared_not_in_unique
        print("Verified: All shared sources remain outside unique ledger and retain independent raw text.")

        results['audit_status'] = 'PASS'
        
        # Save sanitized report (no private memory text, only IDs, counts, and boolean results)
        out_dir = ROOT / "docs" / "tickets" / "p2" / "evidence"
        out_dir.mkdir(parents=True, exist_ok=True)
        report_file = out_dir / "dedup_prompt_audit_report.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\nAudit complete! Sanitized report saved to {report_file}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    run_audit()
