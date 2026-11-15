"""Ticket 363: what VEIL itself knows about each memory, beside Ombre's own record.

Ombre keeps the memory text and its own metadata. A few things are VEIL's to decide and Ombre has no
place for, so they live here as an append-only event log (`<role>/memory-sidecar.jsonl`) that is
folded into a per-memory summary when read:

- locked: Lan locked this memory on the memory page; Peter cannot change, merge away or archive it.
- lan_edited / peter_after_lan: Lan corrected it; Peter rewrote it after that (the page flags this).
- links: "this continues that" links Peter chose when saving a later stage of the same experience
  (Ombre only creates links itself and cannot add one on request).
- sources: when each save or rewrite happened, so the chat around that time can be looked up.
- merged_into / archived_by_merge: this memory was folded into another one and archived, not deleted.

Nothing here holds memory text. A memory that is erased keeps only its ID in this log.
"""
import threading

PATH = 'memory-sidecar.jsonl'
SOURCES_KEPT = 20


class Sidecar:
    def __init__(self, store, role_id, clock):
        self.store, self.path, self.clock = store, f'{role_id}/{PATH}', clock
        self.lock = threading.Lock()

    def _add(self, memory_id, kind, **fields):
        with self.lock:
            self.store.append(self.path, {'memory_id': memory_id, 'kind': kind, **fields})

    def all(self):
        """{memory_id: summary} folded from the whole log, oldest event first."""
        try:
            rows = self.store.read(self.path)
        except Exception:
            rows = []
        out = {}
        for row in rows:
            memory_id, kind = row.get('memory_id'), row.get('kind')
            if not isinstance(memory_id, str) or not isinstance(kind, str):
                continue
            item = out.setdefault(memory_id, {'locked': False, 'lan_edited': None, 'peter_after_lan': None,
                                              'links': [], 'sources': [], 'merged_into': None})
            when = row.get('time')
            if kind == 'lock':
                item['locked'] = True
            elif kind == 'unlock':
                item['locked'] = False
            elif kind == 'lan_edit':
                item['lan_edited'], item['peter_after_lan'] = when, None
            elif kind == 'peter_after_lan':
                item['peter_after_lan'] = when
            elif kind == 'seen':
                item['peter_after_lan'] = None
            elif kind == 'link' and isinstance(row.get('to'), str) and row['to'] not in item['links']:
                item['links'].append(row['to'])
            elif kind == 'unlink':
                item['links'] = [key for key in item['links'] if key != row.get('to')]
            elif kind == 'source':
                item['sources'] = [*item['sources'], {'time': when, 'via': row.get('via', '')}][-SOURCES_KEPT:]
            elif kind == 'merged_into':
                item['merged_into'] = row.get('to') if isinstance(row.get('to'), str) else None
            elif kind == 'unarchived':
                item['merged_into'] = None
        return out

    def get(self, memory_id):
        return self.all().get(memory_id, {'locked': False, 'lan_edited': None, 'peter_after_lan': None,
                                          'links': [], 'sources': [], 'merged_into': None})

    def locked(self, memory_id):
        return self.get(memory_id)['locked']

    def set_locked(self, memory_id, locked):
        self._add(memory_id, 'lock' if locked else 'unlock')

    def lan_edited(self, memory_id):
        self._add(memory_id, 'lan_edit')

    def peter_changed(self, memory_id):
        """Peter changed a memory Lan had corrected: the page shows it until Lan looks or edits again."""
        if self.get(memory_id)['lan_edited']:
            self._add(memory_id, 'peter_after_lan')

    def seen(self, memory_id):
        self._add(memory_id, 'seen')

    def link(self, memory_id, to):
        self._add(memory_id, 'link', to=to)

    def source(self, memory_id, via):
        self._add(memory_id, 'source', via=via)

    def merged_into(self, memory_id, to):
        self._add(memory_id, 'merged_into', to=to)

    def unarchived(self, memory_id):
        self._add(memory_id, 'unarchived')
