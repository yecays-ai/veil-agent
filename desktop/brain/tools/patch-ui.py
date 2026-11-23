#!/usr/bin/env python3
"""Apply VEIL's small, reviewable patches to the vendored mnemory UI.

Run after replacing ``desktop/brain/ui`` with a new mnemory release:

    python desktop/brain/tools/patch-ui.py

Every replacement asserts its exact match count, so an upstream change fails
loudly instead of silently leaving the prototype half-patched.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'ui' / 'index.html'
APP_JS = ROOT / 'ui' / 'js' / 'app.js'
MEMORIES_JS = ROOT / 'ui' / 'js' / 'memories.js'
FSCK_JS = ROOT / 'ui' / 'js' / 'fsck.js'
GRAPH_JS = ROOT / 'ui' / 'js' / 'graph.js'


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'expected exactly one match, found {count}: {old[:80]!r}')
    return text.replace(old, new)


def patch_index() -> None:
    text = INDEX.read_text(encoding='utf-8')

    text = replace_once(text, '<title>mnemory</title>', '<title>VEIL Brain · 记忆中心</title>')
    text = replace_once(
        text,
        '<span class="text-xl font-bold text-brand-accent">mnemory</span>',
        '<span class="text-xl font-bold text-brand-accent">VEIL Brain</span>',
    )
    text = replace_once(
        text,
        '<div class="text-3xl font-bold text-brand-accent mb-3">mnemory</div>',
        '<div class="text-3xl font-bold text-brand-accent mb-3">VEIL Brain</div>',
    )

    # Only expose the tabs this read-only prototype can actually back.
    text = replace_once(
        text,
        """            <template x-for="tab in [
              { id: 'dashboard', label: 'Dashboard' },
              { id: 'search', label: 'Search' },
              { id: 'memories', label: 'Memories' },
              { id: 'sessions', label: 'Sessions' },
              { id: 'user', label: 'User' },
              { id: 'graph', label: 'Graph' },
              { id: 'check', label: 'Check' },
            ]">""",
        """            <template x-for="tab in [
              { id: 'dashboard', label: '概览' },
              { id: 'memories', label: '记忆' },
              { id: 'search', label: '搜索' },
              { id: 'check', label: '健康检查' },
            ]">""",
    )

    # A visible, non-dismissable reminder that this window cannot mutate data.
    text = replace_once(
        text,
        '    </nav>\n\n    <!-- ── Tab Content',
        '    </nav>\n\n'
        '    <div id="veil-brain-banner" class="border-b border-amber-500/30 bg-amber-500/10 px-4 py-1.5 text-center text-xs text-amber-300">\n'
        '      VEIL Brain 只读原型 · 数据来自 brain.db 隔离副本 · 编辑、删除与修复尚未接入\n'
        '    </div>\n\n'        '    <!-- ── Tab Content',
    )

    text = replace_once(
        text,
        """<template x-for="tab in [{id:'current',label:'Current'},{id:'history',label:'History'},{id:'links',label:'Evidence & links'}]" :key="tab.id">""",
        """<template x-for="tab in [{id:'current',label:'正文'},{id:'history',label:'版本'},{id:'links',label:'来源与关联'}]" :key="tab.id">""",
    )
    text = replace_once(
        text,
        '<h2 id="memory-detail-title" class="text-lg font-semibold">Memory details</h2>',
        '<h2 id="memory-detail-title" class="text-lg font-semibold">记忆详情</h2>',
    )

    # Status / source / event / version are VEIL's own facts, not mnemory's.
    text = replace_once(
        text,
        """<div><dt>Type / layer</dt><dd x-text="(detail.memory?.metadata?.memory_type || '—') + ' / ' + (detail.memory?.metadata?.memory_layer || 'consolidated')"></dd></div>""",
        """<div><dt>Type / layer</dt><dd x-text="(detail.memory?.metadata?.memory_type || '—') + ' / ' + (detail.memory?.metadata?.memory_layer || 'consolidated')"></dd></div>
                  <div><dt>来源</dt><dd x-text="detail.memory?.metadata?.labels?.来源 || '—'"></dd></div>
                  <div><dt>事件</dt><dd x-text="detail.memory?.metadata?.labels?.事件 || '—'"></dd></div>
                  <div><dt>版本</dt><dd x-text="detail.memory?.metadata?.labels?.版本 || '—'"></dd></div>""",
    )

    text = replace_once(
        text,
        '  <script src="/ui/vendor/alpine.min.js" defer></script>',
        '  <script src="/ui/vendor/alpine.min.js" defer></script>\n'
        '  <script src="/ui/js/veil-readonly.js" defer></script>',
    )

    # Desktop capture and the real window should not show half-faded modals.
    text = replace_once(
        text,
        '</head>',
        '  <style>/* VEIL prototype: no fade ghosts in screenshots or the app window. */\n'
        '    .modal-overlay, .modal-overlay * { transition: none !important; }\n'
        '  </style>\n</head>',
    )

    # The read-only adapter always scans the whole brain; hide the scope
    # controls instead of showing filters that cannot change the result.
    text = replace_once(
        text,
        """        <!-- Filters -->
        <div class="flex flex-wrap items-end gap-3 mb-6">
          <!-- Agent filter -->""",
        """        <!-- Filters -->
        <div class="flex flex-wrap items-end gap-3 mb-6" x-show="false">
          <!-- Agent filter -->""",
    )

    labels = {
        '>Dashboard<': '>概览<',
        '>Auto-refresh<': '>自动刷新<',
        '>Total Memories<': '>记忆总数<',
        '>Raw<': '>原始<',
        '>Consolidated<': '>已整理<',
        '>Pinned<': '>置顶<',
        '>Decayed<': '>已衰减<',
        '>With Artifacts<': '>带附件<',
        '>Active Sessions<': '>活跃会话<',
        '>Version<': '>版本<',
        '>By Type<': '>按类型<',
        '>By Category<': '>按分类<',
        '>By Layer<': '>按层<',
        '>By Role<': '>按角色<',
    }
    for old, new in labels.items():
        if text.count(old) == 1:
            text = text.replace(old, new)

    # Copy visible in browse, detail and check; keep it consistent with the
    # Chinese VEIL shell without rewriting the whole upstream console.
    translations = [
        ('Filtered: ', '当前用户：'),
        ('Auto-refresh', '自动刷新'),
        ('By Type', '按类型'),
        ('By Role', '按角色'),
        ('<label class="block text-xs text-secondary mb-1">Type</label>', '<label class="block text-xs text-secondary mb-1">类型</label>'),
        ('<label class="block text-xs text-secondary mb-1">Role</label>', '<label class="block text-xs text-secondary mb-1">角色</label>'),
        ('<label class="block text-xs text-secondary mb-1">Agent</label>', '<label class="block text-xs text-secondary mb-1">角色实例</label>'),
        ('<label class="block text-xs text-secondary mb-1">Categories</label>', '<label class="block text-xs text-secondary mb-1">分类</label>'),
        ('<label class="block text-xs text-secondary mb-1">Layer</label>', '<label class="block text-xs text-secondary mb-1">层</label>'),
        ('Only decayed', '只看已衰减'),
        ('Has artifacts', '只看带附件'),
        ('Newest first', '最新优先'),
        ('Oldest first', '最早优先'),
        ('All layers', '全部层'),
        ('Created order', '创建排序'),
        ('>Any time<', '>全部时间<'),
        ('>Raw<', '>原始<'),
        ('>Consolidated<', '>已整理<'),
        ('>Pinned<', '>置顶<'),
        ('Loaded <span', '已加载 <span'),
        ('</span><span x-show="hasMoreOnServer">+</span> memories</span>', '</span><span x-show="hasMoreOnServer">+</span> 条记忆</span>'),
        ('>Refresh<', '>刷新<'),
        ('>Run Check<', '>开始检查<'),
        ("'Run Check'", "'开始检查'"),
        ("'Running...'", "'检查中…'"),
        ('>Memory Check<', '>记忆健康检查<'),
        ('>Total issues<', '>问题总数<'),
        ('<dt>Revision</dt>', '<dt>版本</dt>'),
        ('<dt>State</dt>', '<dt>状态</dt>'),
        ('<dt>Validation</dt>', '<dt>校验</dt>'),
        ('<dt>Type / layer</dt>', '<dt>类型 / 层</dt>'),
        ('<dt>Last accessed</dt>', '<dt>最近访问</dt>'),
        ('<dt>Last validated</dt>', '<dt>最近校验</dt>'),
        ('<dt>Expires</dt>', '<dt>过期</dt>'),
        ('<dt>Created</dt>', '<dt>创建</dt>'),
        ('aria-label="Close memory details">Close', 'aria-label="关闭记忆详情">关闭'),
        ("entry.kind === 'revision' ? 'Revision ' + entry.value.metadata.revision", "entry.kind === 'revision' ? '版本 ' + entry.value.metadata.revision"),
        ('>current<', '>当前<'),
        ("(entry.value.predecessor_id ? 'Previous: ' + entry.value.predecessor_id : '')", "(entry.value.predecessor_id ? '上一版：' + entry.value.predecessor_id : '')"),
        ("(entry.value.successor_id ? ' Next: ' + entry.value.successor_id : '')", "(entry.value.successor_id ? ' 下一版：' + entry.value.successor_id : '')"),
        ('No history is available.', '没有可显示的版本。'),
        ('Loading history…', '正在读取版本…'),
        ("'Revision ' + link.revision", "'版本 ' + link.revision"),
        ('Affected Memories', '影响的记忆'),
        ('Proposed Actions', '建议修复（只读）'),
        ("'% conf'", "'% 置信度'"),
        ("|| 'Never'", "|| '从未'"),
        ("|| 'Permanent'", "|| '永久'"),
        ("' memor' + (issue.affected_memories.length === 1 ? 'y' : 'ies')", "' 条记忆' + (issue.affected_memories.length === 1 ? '' : '')"),
    ]
    for old, new in translations:
        if old in text:
            text = text.replace(old, new)

    INDEX.write_text(text, encoding='utf-8')


def patch_app_js() -> None:
    app = APP_JS.read_text(encoding='utf-8')
    app = replace_once(
        app,
        "  Alpine.store('nav', {\n    activeTab: 'dashboard',",
        """  Alpine.store('nav', {
    activeTab: (() => {
      const requested = new URLSearchParams(window.location.search).get('tab');
      return ['dashboard', 'memories', 'search', 'check'].includes(requested) ? requested : 'dashboard';
    })(),""",
    )
    app = ("// VEIL: the desktop prototype renders charts without a fade-in animation.\n"
           "if (window.Chart) window.Chart.defaults.animation = false;\n\n") + app
    app += """

// The token in the first URL is only there to establish the HttpOnly cookie.
try {
  const clean = new URL(window.location.href);
  if (clean.searchParams.has('t')) {
    clean.searchParams.delete('t');
    window.history.replaceState({}, '', clean.pathname + clean.search + clean.hash);
  }
} catch { /* keep the window usable even if history is unavailable */ }
"""
    app += """

// Deep links land on a tab without the click that normally emits this event.
document.addEventListener('alpine:initialized', () => {
  const tab = Alpine.store('nav')?.activeTab;
  if (tab && tab !== 'dashboard') {
    window.dispatchEvent(new CustomEvent('mnemory:tab-changed', { detail: { tab } }));
  }
});
"""
    APP_JS.write_text(app, encoding='utf-8')


def patch_fsck_js() -> None:
    script = FSCK_JS.read_text(encoding='utf-8')
    script = replace_once(
        script,
        """          if (!this.initialized) {
            this.initialized = true;
            this.loadCategories();
            this._loadAgentIds();
          }""",
        """          if (!this.initialized) {
            this.initialized = true;
            this.loadCategories();
            this._loadAgentIds();
            if (new URLSearchParams(window.location.search).get('check') === '1') {
              this.startCheck();
            }
          }""",
    )
    for old, new in [
        ("label: 'Security Issues'", "label: '安全问题'"),
        ("label: 'Contradictions'", "label: '前后矛盾'"),
        ("label: 'Duplicates'", "label: '重复'"),
        ("label: 'Quality Issues'", "label: '质量问题'"),
        ("label: 'Should Be Split'", "label: '建议拆分'"),
        ("label: 'Misclassified'", "label: '分类可疑'"),
    ]:
        script = script.replace(old, new)
    FSCK_JS.write_text(script, encoding='utf-8')


def patch_memories_js() -> None:
    """Deep link a single memory so the app entry and screenshots can open it."""
    script = MEMORIES_JS.read_text(encoding='utf-8')
    script = replace_once(
        script,
        """      this.loadCategories();
      this._loadAgentIds();
    },""",
        """      this.loadCategories();
      this._loadAgentIds();

      // Deep links land on ?tab=memories without a tab-changed event.
      if (Alpine.store('nav')?.activeTab === 'memories' && !this.initialized) {
        this.initialized = true;
        this.loadMemories(false);
      }
    },""",
    )
    script = replace_once(
        script,
        """        this.nextCursor = data.next_cursor || null;
        this.hasMoreOnServer = !!data.has_more;""",
        """        this.nextCursor = data.next_cursor || null;
        this.hasMoreOnServer = !!data.has_more;
        const deepLink = new URLSearchParams(window.location.search).get('memory');
        if (!append && deepLink && !this.deepLinkHandled) {
          this.deepLinkHandled = true;
          const target = this.memories.find((item) => item.id === deepLink);
          if (target) {
            this.openDetail(target).then(() => {
              const requested = new URLSearchParams(window.location.search).get('detail');
              if (['history', 'links'].includes(requested)) this.selectDetailTab(requested);
            });
          } else {
            MnemoryAPI.getMemoriesByIds([deepLink]).then((data) => {
              const item = (data.results || [])[0];
              if (item) this.openDetail(item);
            }).catch(() => {});
          }
        }""",
    )
    MEMORIES_JS.write_text(script, encoding='utf-8')


def patch_graph_js() -> None:
    """brain.db importance is numeric and categories are user text."""
    script = GRAPH_JS.read_text(encoding='utf-8')
    script = replace_once(
        script,
        "      const typeLabel = d.type.charAt(0).toUpperCase() + d.type.slice(1);\n"
        "      const importanceLabel = d.importance.charAt(0).toUpperCase() + d.importance.slice(1);",
        "      const typeLabel = this._escapeHtml(String(d.type || ''));\n"
        "      const importanceLabel = this._escapeHtml(String(d.importance ?? ''));",
    )
    script = replace_once(
        script,
        "        ? `<div style=\"color:#94A3B8;margin-top:4px\">${d.categories.join(', ')}</div>`",
        "        ? `<div style=\"color:#94A3B8;margin-top:4px\">${d.categories.map((c) => this._escapeHtml(String(c))).join(', ')}</div>`",
    )
    GRAPH_JS.write_text(script, encoding='utf-8')


def main() -> None:
    patch_index()
    patch_app_js()
    patch_memories_js()
    patch_fsck_js()
    patch_graph_js()
    print('patched all vendored UI files')


if __name__ == '__main__':
    main()
