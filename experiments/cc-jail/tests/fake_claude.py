"""Fake 'claude' used to test the runner's limits. Mode is picked by the prompt text on stdin."""
import json
import sys
import time

mode = sys.stdin.read().strip()


def emit(o):
    print(json.dumps(o), flush=True)


def tool(name, inp):
    emit({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': name, 'input': inp}]}})


if mode == 'repeat':
    for _ in range(10):
        tool('mcp__sbx__sandbox_exec', {'command': 'echo same'})
        time.sleep(0.2)
elif mode == 'hang':
    tool('mcp__sbx__sandbox_exec', {'command': 'echo hi'})
    time.sleep(120)
else:
    tool('mcp__sbx__sandbox_exec', {'command': 'echo ok'})
    emit({'type': 'result', 'result': 'done', 'total_cost_usd': 0, 'num_turns': 1, 'subtype': 'success'})
