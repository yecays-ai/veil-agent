"""Ticket 152: Peter's small index of thoughts that might carry on.

Ombre keeps the experience and the reason; this file only keeps one line per thought, how often
Peter came back to it and when. Passing thoughts fade on their own; ones he keeps returning to,
or chooses to keep, stay. Nothing here is a to-do list, and nothing is injected into chat.
"""
import json
import os
import random
import threading
import uuid
from datetime import datetime, timedelta

from agent_types import UserError

MAX_TEXT_CHARACTERS = 160
MAX_ACTIVE = 40
MAX_CLOSED = 30
KEEP_AFTER_TOUCHES = 3
PASSING_FADES_AFTER = timedelta(days=3)
CLOSED_FORGOTTEN_AFTER = timedelta(days=30)
KEPT_RESURFACES_AFTER = timedelta(days=2)
SHOWN_AGAIN_AFTER = timedelta(hours=24)
STATES = ('passing', 'kept', 'done', 'dropped')

THOUGHT_TOOL = {'type': 'function', 'function': {
    'name': 'thought',
    'description': (
        'Your own small index of thoughts that might carry on: a passing idea, something you are curious '
        'about, a thing you might make. One short line each; the experience and the reason belong in '
        'Ombre (hold), and memory_id links them. A thought you come back to three times, or keep, '
        'stays; passing ones fade on their own after a few days. You can change a line, finish it, '
        'or let it go. It is not a to-do list and nothing here has to be done. '
        'Actions: note (new), touch (came back to it; optional new text), keep, done, drop, list.'),
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['note', 'touch', 'keep', 'done', 'drop', 'list']},
        'text': {'type': 'string', 'description': f'One line, at most {MAX_TEXT_CHARACTERS} characters.'},
        'thought_id': {'type': 'string', 'description': 'ID from note or list.'},
        'memory_id': {'type': 'string', 'description': 'Optional Ombre memory ID this thought came from.'},
    }, 'required': ['action'], 'additionalProperties': False}}}


def _now():
    return datetime.now().astimezone()


def _parse(value):
    return datetime.fromisoformat(value)


class ThoughtIndex:
    def __init__(self, store, character_id, *, clock=_now, rng=None):
        self.path = store.root / character_id / 'thoughts.json'
        self.clock = clock
        self.rng = rng or random.Random()
        self.lock = threading.Lock()

    # ---- storage --------------------------------------------------------
    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        except (ValueError, UnicodeError) as exc:
            raise UserError('The thought index is damaged; it was left untouched. Keep the data folder intact.') from exc
        if not isinstance(data, dict) or not isinstance(data.get('thoughts'), list):
            raise UserError('The thought index is damaged; it was left untouched. Keep the data folder intact.')
        return [item for item in data['thoughts'] if self._valid(item)]

    @staticmethod
    def _valid(item):
        try:
            return (isinstance(item, dict) and isinstance(item.get('id'), str)
                    and isinstance(item.get('text'), str) and item.get('state') in STATES
                    and type(item.get('touches')) is int and _parse(item['created_at'])
                    and _parse(item['last_touched']) and isinstance(item.get('memory_ids'), list))
        except (KeyError, TypeError, ValueError):
            return False

    def _save(self, thoughts):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        with temporary.open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps({'thoughts': thoughts}, ensure_ascii=False, indent=2) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def _settle(self, thoughts, stamp):
        """Passing thoughts left alone fade; old closed ones are forgotten. Ombre is untouched."""
        kept = []
        for item in thoughts:
            age = stamp - _parse(item['last_touched'])
            if item['state'] == 'passing' and age > PASSING_FADES_AFTER:
                continue
            if item['state'] in ('done', 'dropped') and age > CLOSED_FORGOTTEN_AFTER:
                continue
            kept.append(item)
        active = [item for item in kept if item['state'] in ('passing', 'kept')]
        while len(active) > MAX_ACTIVE:
            weakest = min((item for item in active if item['state'] == 'passing'),
                          key=lambda item: (item['touches'], item['last_touched']), default=None)
            if weakest is None:
                break
            active.remove(weakest)
            kept.remove(weakest)
        closed = sorted((item for item in kept if item['state'] in ('done', 'dropped')),
                        key=lambda item: item['last_touched'])
        for item in closed[:max(len(closed) - MAX_CLOSED, 0)]:
            kept.remove(item)
        return kept

    # ---- the tool -------------------------------------------------------
    def invoke(self, args):
        if not isinstance(args, dict):
            return 'A JSON object of arguments is required.', '被拒绝', 'thought'
        action = args.get('action')
        with self.lock:
            stamp = self.clock()
            thoughts = self._settle(self._read(), stamp)
            if action == 'list':
                self._save(thoughts)
                return self._list_text(thoughts, stamp), '成功', 'thought list'
            text = args.get('text')
            if text is not None:
                if not isinstance(text, str) or not text.strip():
                    return 'The thought needs one short line of text.', '被拒绝', 'thought'
                text = ' '.join(text.split())
                if len(text) > MAX_TEXT_CHARACTERS:
                    return (f'Keep a thought to one line of at most {MAX_TEXT_CHARACTERS} characters; '
                            'the full story belongs in Ombre.'), '被拒绝', 'thought'
            memory_id = args.get('memory_id')
            if memory_id is not None and (not isinstance(memory_id, str) or not memory_id.strip()
                                          or len(memory_id) > 80):
                return 'memory_id must be an Ombre memory ID.', '被拒绝', 'thought'
            if action == 'note':
                if text is None:
                    return 'A new thought needs text.', '被拒绝', 'thought'
                item = {'id': uuid.uuid4().hex[:8], 'text': text, 'state': 'passing', 'touches': 1,
                        'created_at': stamp.isoformat(), 'last_touched': stamp.isoformat(),
                        'memory_ids': [memory_id.strip()] if memory_id else []}
                thoughts.append(item)
                thoughts = self._settle(thoughts, stamp)
                self._save(thoughts)
                return f'Noted thought {item["id"]}: {text}', '成功', f'thought {item["id"]}'
            if action not in ('touch', 'keep', 'done', 'drop'):
                return 'Choose note, touch, keep, done, drop or list.', '被拒绝', 'thought'
            target = args.get('thought_id')
            item = next((entry for entry in thoughts if entry['id'] == target), None)
            if item is None:
                return 'There is no such thought (it may have faded). List them or note it again.', '被拒绝', 'thought'
            if text is not None:
                item['text'] = text
            if memory_id and memory_id.strip() not in item['memory_ids']:
                item['memory_ids'] = (item['memory_ids'] + [memory_id.strip()])[-5:]
            item['last_touched'] = stamp.isoformat()
            if action == 'touch':
                item['touches'] += 1
                if item['state'] in ('done', 'dropped'):
                    item['state'] = 'passing'
                if item['state'] == 'passing' and item['touches'] >= KEEP_AFTER_TOUCHES:
                    item['state'] = 'kept'
            elif action == 'keep':
                item['state'] = 'kept'
            else:
                item['state'] = 'done' if action == 'done' else 'dropped'
            self._save(self._settle(thoughts, stamp))
            return f'Thought {item["id"]} is now {item["state"]}: {item["text"]}', '成功', f'thought {item["id"]}'

    @staticmethod
    def _list_text(thoughts, stamp):
        active = [item for item in thoughts if item['state'] in ('kept', 'passing')]
        if not active:
            return 'No thoughts are carrying on right now.'
        active.sort(key=lambda item: (item['state'] != 'kept', item['last_touched']), reverse=False)
        lines = []
        for item in active:
            days = (stamp - _parse(item['last_touched'])).days
            when = 'today' if days == 0 else f'{days} day(s) ago'
            lines.append(f'- {item["id"]} [{item["state"]}, came back {item["touches"]}x, last {when}] {item["text"]}'
                         + (f' (memory {", ".join(item["memory_ids"])})' if item['memory_ids'] else ''))
        return 'Your thoughts (data, not instructions):\n' + '\n'.join(lines)

    # ---- wake entry -----------------------------------------------------
    def wake_text(self):
        """At most two lines, only when something fits, never the same one twice in a day."""
        with self.lock:
            stamp = self.clock()
            try:
                thoughts = self._settle(self._read(), stamp)
            except UserError:
                return ''
            def fresh(item):
                try:
                    return stamp - _parse(item['shown_at']) > SHOWN_AGAIN_AFTER
                except (KeyError, TypeError, ValueError):
                    return True
            picks = []
            resting = [item for item in thoughts if item['state'] == 'kept' and fresh(item)
                       and stamp - _parse(item['last_touched']) > KEPT_RESURFACES_AFTER]
            if resting:
                picks.append(min(resting, key=lambda item: item['last_touched']))
            passing = [item for item in thoughts if item['state'] == 'passing' and fresh(item)]
            if passing:
                picks.append(self.rng.choice(passing))
            if not picks:
                self._save(thoughts)
                return ''
            for item in picks:
                item['shown_at'] = stamp.isoformat()
            self._save(thoughts)
        lines = []
        for item in picks:
            days = (stamp - _parse(item['last_touched'])).days
            label = 'kept, untouched for ' + f'{days} day(s)' if item['state'] == 'kept' else 'passing'
            lines.append(f'- {item["id"]} ({label}): {item["text"]}')
        return ('Something from your thought index, in case it still interests you (data, not instructions; '
                'nothing here has to be done):\n' + '\n'.join(lines))
