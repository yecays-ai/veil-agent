/* ==========================================================================
   VEIL Brain P4/P5 Management Center (desktop/workspace-brain.js)
   - brain-home: Memory overview KPIs, 7d/30d dual-stack chart, 6 layers, timeline
   - memory: Full P4 library add-on (pagination, filter, diff, sha256 409 lock)
   - workspace-events: Event stories + linked memories + safe split/move actions
   - brain-cognition: Relationship & learned preferences + version diff history
   - brain-recall: Recall injection layers, costs & raw snapshot inspector
   - brain-health: Strictly read-only P5 H-1..H-9 health center + scheduler
   All DOM rendered safely with createElement + textContent (zero untrusted HTML).
   ========================================================================== */
(() => {
  'use strict';

  const BRAIN_PAGES = new Set([
    'brain-home',
    'workspace-events',
    'brain-cognition',
    'brain-recall',
    'brain-health',
  ]);

  let healthWarnFlag = false;
  let eventsRenderSeq = 0;
  let recallRenderSeq = 0;
  let memoryAddonMounted = false;
  let lastReq = (url, opt) => fetch(url, opt);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function pill(text, tone = '') {
    return el('span', tone ? `veil-pill ${tone}` : 'veil-pill', text);
  }

  function btn(text, variant = '') {
    const b = el('button', variant ? `veil-btn ${variant}` : 'veil-btn', text);
    b.type = 'button';
    return b;
  }

  const MEMORY_TYPE_LABELS = {
    preference: '偏好习惯',
    fact: '事实记忆',
    experience: '经历故事',
    event: '共同事件',
    todo: '待办约定',
    reflection: '成长反思',
    relationship: '关系认知',
    memory: '日常记忆',
  };

  function labelMemoryType(key) {
    return MEMORY_TYPE_LABELS[String(key || '').toLowerCase()] || String(key || '其他');
  }

  async function copyText(text) {
    const str = String(text || '');
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(str);
        return true;
      } catch {
        // fallback below
      }
    }
    try {
      const ta = document.createElement('textarea');
      ta.value = str;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }

  function formatTime(iso) {
    if (!iso) return '—';
    return String(iso).replace('T', ' ').slice(0, 19);
  }

  function shortSha(sha) {
    return sha ? String(sha).slice(0, 8) : '—';
  }

  async function reqJson(req, url, options) {
    // A dropped connection must surface as a failed request, not as an unhandled rejection that
    // leaves a just-disabled control impossible to retry. Write requests are never auto-retried:
    // the server may already have committed.
    try {
      const res = await req(url, options);
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      return { ok: false, status: 0, data: null, error: err };
    }
  }

  function buildLineDiffNode(diffLines) {
    const box = el('div', 'diff-box');
    if (!Array.isArray(diffLines) || !diffLines.length) {
      box.textContent = '（内容无文本差异）';
      return box;
    }
    for (const line of diffLines) {
      const span = el('div', '', line);
      if (line.startsWith('+') && !line.startsWith('+++')) span.className = 'diff-add';
      else if (line.startsWith('-') && !line.startsWith('---')) span.className = 'diff-del';
      box.append(span);
    }
    return box;
  }

  function buildReadableDiffNode(diffLines, oldText = '', newText = '') {
    const wrap = el('div', 'stack-sm');
    let beforeLines = [];
    let afterLines = [];
    if (Array.isArray(diffLines) && diffLines.length) {
      for (const ln of diffLines) {
        if (ln.startsWith('---') || ln.startsWith('+++') || ln.startsWith('@@')) continue;
        if (ln.startsWith('-')) beforeLines.push(ln.slice(1).trimStart());
        else if (ln.startsWith('+')) afterLines.push(ln.slice(1).trimStart());
        else {
          const clean = ln.startsWith(' ') ? ln.slice(1) : ln;
          beforeLines.push(clean);
          afterLines.push(clean);
        }
      }
    }
    const beforeStr = beforeLines.join('\n').trim() || String(oldText || '').trim();
    const afterStr = afterLines.join('\n').trim() || String(newText || '').trim();

    if (beforeStr || afterStr) {
      const grid = el('div', 'diff-compare-grid');
      const colB = el('div', 'diff-compare-col before');
      colB.append(el('span', 'diff-compare-label', '修改前'), document.createTextNode(beforeStr || '（新增内容，此前无记录）'));
      const colA = el('div', 'diff-compare-col after');
      colA.append(el('span', 'diff-compare-label', '修改后'), document.createTextNode(afterStr || '（已清空或移除）'));
      grid.append(colB, colA);
      wrap.append(grid);
    }
    if (Array.isArray(diffLines) && diffLines.length) {
      const rawDet = el('details', 'tech-evidence');
      rawDet.append(el('summary', '', '查看原始行级 Diff'), buildLineDiffNode(diffLines));
      wrap.append(rawDet);
    }
    return wrap;
  }

  function openBrainModal(title, subtitle, buildContent) {
    const existing = document.getElementById('veil-brain-modal');
    if (existing) existing.remove();

    const backdrop = el('div', 'veil-modal-backdrop');
    backdrop.id = 'veil-brain-modal';
    const card = el('div', 'veil-modal-card wide');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    const head = el('div', 'veil-modal-head');
    const titleBox = el('div');
    titleBox.append(el('h3', '', title));
    if (subtitle) titleBox.append(el('small', 'muted', subtitle));
    const closeBtn = btn('关闭', 'sm');
    const closeModal = () => {
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
    };
    const onKey = e => { if (e.key === 'Escape') closeModal(); };
    closeBtn.addEventListener('click', closeModal);
    backdrop.addEventListener('click', e => { if (e.target === backdrop) closeModal(); });
    document.addEventListener('keydown', onKey);

    head.append(titleBox, closeBtn);
    const body = el('div', 'stack-sm');
    buildContent(body, closeModal);
    card.append(head, body);
    backdrop.append(card);
    document.body.append(backdrop);
    return closeModal;
  }

  function renderEmptyState(title, subtitle) {
    const box = el('div', 'empty-pane');
    box.append(el('span', 'empty-pane-mark'), el('strong', '', title), el('small', '', subtitle));
    return box;
  }

  function memorySwitchRow(label, checked, detail, onChange) {
    const item = el('div', 'layer-item');
    const top = el('div', 'layer-item-top');
    top.append(el('strong', '', label));
    const sw = el('button', 'workspace-switch');
    sw.type = 'button';
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', String(Boolean(checked)));
    sw.setAttribute('aria-label', label + '开关');
    sw.title = checked ? '点击关闭' : '点击启用';
    sw.addEventListener('click', () => {
      const next = !Boolean(checked);
      checked = next;
      sw.setAttribute('aria-checked', String(next));
      onChange(next);
    });
    top.append(sw);
    item.append(top);
    if (detail) item.append(el('small', '', detail));
    return item;
  }

  function memoryNumberRow(label, value, options, onChange) {
    const item = el('div', 'layer-item');
    item.append(el('strong', '', label));
    const input = el('input', 'veil-input');
    input.type = 'number';
    input.value = String(value);
    if (options.min !== undefined) input.min = String(options.min);
    if (options.max !== undefined) input.max = String(options.max);
    if (options.step !== undefined) input.step = String(options.step);
    input.style.maxWidth = '140px';
    input.addEventListener('change', () => {
      const num = Number(input.value);
      if (!Number.isFinite(num)) {
        input.value = String(value);
        return;
      }
      value = num;
      onChange(num);
    });
    item.append(input);
    if (options.detail) item.append(el('small', '', options.detail));
    return item;
  }

  // P6-S6-1: the experimental switches and parameters live in this panel and are persisted in
  // data/<role>/memory-weights.json. The note states the hard guards so the UI never implies that
  // a toggle can weaken them.
  function buildMemoryWeightsPanel(req, host, data) {
    const draft = { ...(data.settings || {}) };
    const sub = el('div', 'veil-subpanel');
    sub.style.marginTop = '10px';
    const head = el('div', 'veil-card-head');
    const counters = data.counters || {};
    head.append(
      el('strong', '', '记忆权重与保护性衰减'),
      pill('策略 ' + (data.policy_version || 's6-1'), ''),
      pill('已跟踪 ' + (counters.tracked_memories || 0) + ' 条', ''),
    );
    const grid = el('div', 'six-layer-grid');
    grid.style.gridTemplateColumns = 'repeat(auto-fit, minmax(240px, 1fr))';
    grid.append(
      memorySwitchRow('送达后激活', draft.activation_enabled === true,
        '只有真正送达你的回复才更新 last_active 与强化次数；取消、失败、重启不写入。',
        v => { draft.activation_enabled = v; }),
      memorySwitchRow('保护性衰减', draft.decay_enabled === true,
        '只降低 importance<=6 且未置顶记忆的活跃度；importance>=7 与置顶永不衰减，衰减不删除、不隐藏。',
        v => { draft.decay_enabled = v; }),
      memorySwitchRow('活跃度参与排序（实验）', draft.ranking_enabled === true,
        '#455 的 AB-R5b 证据不足，默认关闭；打开后只加在检索分数上，不改重要度。',
        v => { draft.ranking_enabled = v; }),
      memorySwitchRow('动态重评', draft.reeval_enabled === true,
        '新经历会生成有证据、可追溯的版本化重评建议。',
        v => { draft.reeval_enabled = v; }),
      memorySwitchRow('自动应用安全增量', draft.reeval_auto_apply === true,
        '只自动提升到 ' + (draft.reeval_max_auto_importance || 7) + ' 及以下；降权、置顶、锁定与重要记忆只提建议。',
        v => { draft.reeval_auto_apply = v; }),
    );
    grid.append(
      memoryNumberRow('衰减半衰期（天）', draft.decay_half_life_days, {
        min: 7, max: 365, step: 1, detail: '越大，低重要度记忆的活跃度消退越慢。',
      }, v => { draft.decay_half_life_days = v; }),
      memoryNumberRow('活跃度排序权重', draft.ranking_weight, {
        min: 0, max: 0.2, step: 0.01,
        detail: '现网 importance 静态加分上限为 0.05；默认 0.05，排序开关关闭时不生效。',
      }, v => { draft.ranking_weight = v; }),
      memoryNumberRow('重评冷却（小时）', draft.reeval_cooldown_hours, {
        min: 1, max: 720, step: 1, detail: '同一条记忆两次重评之间的最小间隔。',
      }, v => { draft.reeval_cooldown_hours = v; }),
      memoryNumberRow('自动上限重要度', draft.reeval_max_auto_importance, {
        min: 5, max: 7, step: 1, detail: '自动提升不会越过这个值，硬上限 7；更高区间需要你确认。',
      }, v => { draft.reeval_max_auto_importance = v; }),
    );
    const actions = el('div', 'workspace-links');
    const saveBtn = btn('保存并下一轮生效', 'sm primary');
    const proposalsBtn = btn('重评建议（' + (counters.proposals_pending || 0) + '）', 'sm');
    const statusLine = el('small', 'muted', '');
    saveBtn.addEventListener('click', async () => {
      saveBtn.disabled = true;
      statusLine.textContent = '正在校验并持久化…';
      const res = await reqJson(req, '/api/brain/memory-weights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values: draft }),
      });
      saveBtn.disabled = false;
      if (!res.ok || !res.data?.ok) {
        statusLine.textContent = res.data?.error || '保存失败，已保留原设置。';
        return;
      }
      const rejected = res.data.rejected || {};
      if (Object.keys(rejected).length) {
        statusLine.textContent = '已保存；被拒绝的参数：' + Object.keys(rejected).join('、');
      } else {
        statusLine.textContent = '已保存到 ' + (res.data.persistence_path || '本机数据目录') + '，下一轮生效。';
      }
      if (typeof renderBrainHome === 'function') renderBrainHome(host, req);
    });
    proposalsBtn.addEventListener('click', () => openMemoryProposals(req, '', () => renderBrainHome(host, req)));
    actions.append(saveBtn, proposalsBtn);
    sub.append(head, grid, actions, statusLine);
    return sub;
  }

  async function openMemoryProposals(req, memoryId, refresh) {
    const query = '/api/brain/memory-proposals?status=pending'
      + (memoryId ? '&memory_id=' + encodeURIComponent(memoryId) : '');
    const res = await reqJson(req, query);
    const items = (res.data && res.data.items) || [];
    openBrainModal('重评建议', '证据驱动的长期重要度建议；降权、置顶、锁定与重要记忆都要你确认，每次应用都会留下版本', body => {
      if (!items.length) {
        body.append(el('p', '', '当前没有待确认的重评建议。'));
        return;
      }
      for (const p of items) {
        const card = el('div', 'veil-subpanel');
        card.append(el('strong', '', (p.title || p.memory_id) + ' · 重要度 ' + p.current_importance + ' → ' + p.proposed_importance));
        card.append(el('small', 'muted', '置信度 ' + Math.round((p.confidence || 0) * 100) + '% · 触发 ' + (p.trigger || '') + ' · 策略 ' + (p.policy_version || '')));
        if (Array.isArray(p.reasons) && p.reasons.length) {
          const ul = el('ul', 'flat-list');
          for (const r of p.reasons) ul.append(el('li', '', (r.label || r.code) + '：' + (r.detail || '')));
          card.append(ul);
        }
        if (Array.isArray(p.evidence) && p.evidence.length) {
          const ev = el('details', 'tech-evidence');
          ev.append(el('summary', '', '查看证据引用'));
          const ul = el('ul', 'flat-list');
          for (const e of p.evidence) ul.append(el('li', '', (e.kind || '') + ' · ' + (e.ref || '') + ' · ' + (e.detail || '')));
          ev.append(ul);
          card.append(ev);
        }
        const actions = el('div', 'workspace-links');
        const accept = btn('接受并写入版本', 'sm primary');
        const reject = btn('拒绝', 'sm');
        const line = el('small', 'muted', '');
        accept.addEventListener('click', async () => {
          accept.disabled = true;
          const out = await reqJson(req, '/api/brain/memory-proposal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposal_id: p.proposal_id, action: 'accept' }),
          });
          accept.disabled = false;
          line.textContent = out.ok && out.data?.ok ? '已应用，可在记忆详情里查看版本或撤回。' : (out.data?.error || '应用失败。');
          if (out.ok && out.data?.ok) {
            if (typeof refresh === 'function') refresh();
            card.remove();
          }
        });
        reject.addEventListener('click', async () => {
          reject.disabled = true;
          const out = await reqJson(req, '/api/brain/memory-proposal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposal_id: p.proposal_id, action: 'reject' }),
          });
          reject.disabled = false;
          line.textContent = out.ok && out.data?.ok ? '已拒绝；同类建议近期不会重复。' : (out.data?.error || '拒绝失败。');
          if (out.ok && out.data?.ok) {
            if (typeof refresh === 'function') refresh();
            card.remove();
          }
        });
        actions.append(accept, reject);
        card.append(actions, line);
        body.append(card);
      }
    });
  }

  async function renderMemoryReevalHistory(req, item, refresh) {
    const res = await reqJson(req, '/api/brain/memory-proposals?memory_id=' + encodeURIComponent(item.memory_id) + '&limit=30');
    const box = el('div', 'veil-subpanel');
    box.append(el('strong', '', '重评历史'));
    const items = (res.data && res.data.items) || [];
    if (!items.length) {
      box.append(el('p', '', '还没有重评记录。新经历积累到有证据时会自动生成。'));
      return box;
    }
    for (const p of items) {
      const row = el('div', 'flat-entry');
      const statusLabel = { applied: '已应用', pending: '待确认', rejected: '已拒绝', reverted: '已撤回', superseded: '已失效' }[p.status] || p.status;
      row.append(el('strong', '', '重要度 ' + p.current_importance + ' → ' + p.proposed_importance + ' · ' + statusLabel));
      row.append(el('small', 'muted', (p.created_at || '') + ' · ' + (p.trigger || '') + (p.auto_applied ? ' · 自动' : '')));
      const ul = el('ul', 'flat-list');
      for (const r of p.reasons || []) ul.append(el('li', '', (r.label || r.code) + '：' + (r.detail || '')));
      row.append(ul);
      if (p.status === 'applied') {
        const revert = btn('撤回这次重评', 'sm');
        const line = el('small', 'muted', '');
        revert.addEventListener('click', async () => {
          revert.disabled = true;
          const out = await reqJson(req, '/api/brain/memory-proposal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposal_id: p.proposal_id, action: 'revert', note: '界面撤回' }),
          });
          revert.disabled = false;
          line.textContent = out.ok && out.data?.ok ? '已撤回，重要度回到 ' + out.data.importance + '。' : (out.data?.error || '撤回失败。');
          if (out.ok && out.data?.ok && typeof refresh === 'function') refresh();
        });
        row.append(revert, line);
      }
      box.append(row);
    }
    return box;
  }

  // =========================================================================
  // 1. P4 Memory Overview (brain-home)
  // =========================================================================
  async function renderBrainHome(host, req) {
    host.replaceChildren(el('p', '', '正在汇总 VEIL Brain 记忆监控数据…'));
    const [{ ok, data }, { ok: gOk, data: gData }] = await Promise.all([
      reqJson(req, '/api/brain/overview'),
      reqJson(req, '/api/brain/growth?range=7d'),
    ]);
    if (!ok || !data || !data.counts) {
      host.replaceChildren(renderEmptyState('暂时无法读取记忆监控数据', '请确认当前角色目录与后端服务正常。'));
      return;
    }

    const c = data.counts;
    const hSum = data.health_summary;
    healthWarnFlag = Boolean(hSum && (hSum.warn_count || hSum.error_count));

    // 1. Top KPI row
    const kpiGrid = el('div', 'kpi-grid');
    const kpiDefs = [
      { label: '活跃记忆', val: c.active ?? 0, sub: `置顶 ${c.pinned ?? 0} · 归档 ${c.archived ?? 0}`, page: 'memory' },
      { label: '共同经历', val: c.events_total ?? 0, sub: `待重整 ${c.events_pending_story ?? 0} · 待复核 ${c.events_pending_review ?? 0}`, page: 'memory' },
      { label: '本周新增', val: `+${c.week_added ?? 0}`, sub: `待补向量 ${c.pending_vector ?? 0} · 墓碑 ${c.tombstones ?? 0}` },
      { label: '本周修订', val: `${c.week_updated ?? 0}`, sub: `最后更新 ${formatTime((gData && gData.summary && gData.summary.last_updated_at) || new Date().toISOString())}` },
    ];
    for (const k of kpiDefs) {
      const card = el('article', 'veil-card kpi-card');
      const top = el('div', 'kpi-top');
      top.append(el('span', '', k.label), k.page ? el('span', 'kpi-arrow', '→') : el('span', '', ''));
      card.append(top, el('div', 'kpi-val', k.val), el('div', 'kpi-sub', k.sub));
      if (k.page) card.addEventListener('click', () => window.veilWorkspace?.go(k.page));
      kpiGrid.append(card);
    }

    // 2. Six-Layer Memory System (Compact 3x2 grid & real toggles)
    const layerCard = el('article', 'veil-card');
    const layerHead = el('div', 'veil-card-head');
    layerHead.append(el('h3', '', '六层记忆系统'), el('span', 'muted', '运行状态与控制'));
    const layerGrid = el('div', 'six-layer-grid');
    for (const layer of data.layers || []) {
      const item = el('div', 'layer-item');
      const top = el('div', 'layer-item-top');
      top.append(el('strong', '', layer.name));
      if (layer.toggle_key && !layer.protected) {
        const sw = el('button', 'workspace-switch');
        sw.type = 'button';
        sw.setAttribute('role', 'switch');
        sw.setAttribute('aria-checked', String(Boolean(layer.enabled)));
        sw.setAttribute('aria-label', `${layer.name}开关`);
        sw.title = layer.enabled ? '点击关闭' : '点击启用';
        sw.addEventListener('click', async () => {
          const nextVal = !Boolean(layer.enabled);
          sw.disabled = true;
          const res = await reqJson(req, '/api/brain/layer-toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layer_id: layer.id, enabled: nextVal }),
          });
          sw.disabled = false;
          if (!res.ok || !res.data?.ok) {
            alert(res.data?.error || '切换失败，已恢复原状态');
            return;
          }
          layer.enabled = Boolean(res.data.enabled);
          sw.setAttribute('aria-checked', String(layer.enabled));
          renderBrainHome(host, req);
        });
        top.append(sw);
      } else {
        top.append(pill(layer.status_text || '受保护', layer.tone || 'good'));
      }
      item.append(top);
      if (layer.toggle_key && !layer.protected && layer.status_text) {
        item.append(pill(layer.status_text, layer.tone || ''));
      }
      item.append(el('small', '', layer.detail || ''));
      if (layer.protected && layer.protected_reason) {
        item.append(el('small', 'muted', layer.protected_reason));
      }
      if (layer.id === 'cognition' && typeof layer.budget_max === 'number') {
        const meter = el('div', 'budget-meter');
        const fill = el('div');
        const pct = Math.min(100, Math.round(((layer.budget_used || 0) / Math.max(1, layer.budget_max)) * 100));
        fill.style.width = `${pct}%`;
        fill.style.background = pct > 90 ? 'var(--bad, #c0392b)' : 'var(--veil-accent)';
        meter.append(fill);
        item.append(meter);
      }
      layerGrid.append(item);
    }
    layerCard.append(layerHead, layerGrid);

    // 2b. Natural Association Controls (inside existing memory config card, persisted via /api/brain/layer-toggle)
    if (data.recall_association && Array.isArray(data.recall_association.controls) && data.recall_association.controls.length) {
      const assoc = data.recall_association;
      const assocSub = el('div', 'veil-subpanel');
      assocSub.style.marginTop = '10px';
      const assocHead = el('div', 'veil-card-head');
      const modeTone = assoc.effective_mode === 'disabled' ? '' : 'good';
      const leisureTone = assoc.leisure_effective ? 'good' : (assoc.leisure_enabled ? 'warn' : '');
      const assocHeadBadges = el('div', 'inline-pills');
      assocHeadBadges.append(
        pill(`聊天生效：${assoc.status_text || assoc.effective_mode}`, modeTone),
        pill(`自主闲暇：${assoc.leisure_status_text || (assoc.leisure_effective ? '已启用' : '已关闭')}`, leisureTone),
      );
      assocHead.append(
        el('strong', '', '自然联想控制'),
        assocHeadBadges,
      );
      const assocGrid = el('div', 'six-layer-grid');
      assocGrid.style.gridTemplateColumns = 'repeat(auto-fit, minmax(240px, 1fr))';
      for (const ctrl of assoc.controls) {
        const item = el('div', 'layer-item');
        const top = el('div', 'layer-item-top');
        top.append(el('strong', '', ctrl.name));
        const sw = el('button', 'workspace-switch');
        sw.type = 'button';
        sw.setAttribute('role', 'switch');
        sw.setAttribute('aria-checked', String(Boolean(ctrl.enabled)));
        sw.setAttribute('aria-label', `${ctrl.name}开关`);
        sw.title = ctrl.enabled ? '点击关闭' : '点击启用';
        sw.addEventListener('click', async () => {
          const nextVal = !Boolean(ctrl.enabled);
          sw.disabled = true;
          const res = await reqJson(req, '/api/brain/layer-toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layer_id: ctrl.id, enabled: nextVal }),
          });
          sw.disabled = false;
          if (!res.ok || !res.data?.ok) {
            alert(res.data?.error || '切换失败，已恢复原状态');
            return;
          }
          ctrl.enabled = Boolean(res.data.enabled);
          sw.setAttribute('aria-checked', String(ctrl.enabled));
          renderBrainHome(host, req);
        });
        top.append(sw);
        item.append(top);
        if (ctrl.status_text) {
          item.append(pill(ctrl.status_text, ctrl.tone || ''));
        }
        item.append(el('small', '', ctrl.detail || ''));
        assocGrid.append(item);
      }
      assocSub.append(assocHead, assocGrid);
      const ldiag = assoc.leisure_diagnostics;
      if (ldiag && typeof ldiag === 'object') {
        const diagLine = el('div', 'muted');
        diagLine.style.marginTop = '6px';
        diagLine.style.fontSize = '12px';
        if (ldiag.recent_wakes > 0) {
          const simText = typeof ldiag.last_similarity === 'number' ? ` · 相似度 ${ldiag.last_similarity.toFixed(2)}` : '';
          const auditText = ldiag.last_audit_clean === false
            ? ' · 输出审计：检测到旁白或无工具扮演'
            : (ldiag.last_audit_clean === true ? ' · 输出审计：正常' : '');
          diagLine.textContent = `自主闲暇诊断（近 ${ldiag.recent_wakes} 次唤醒）：闲暇 ${ldiag.leisure_wakes} 次 / 联想命中 ${ldiag.hits} 次 / 任务态拦截 ${ldiag.task_blocked} 次 · 最近：${ldiag.last_wake_mode || '未知'}（${ldiag.last_reason || '无'}）${simText}${auditText}`;
        } else {
          diagLine.textContent = '自主闲暇诊断：尚无开启后的自主唤醒运行记录（默认关闭，开启后下次闲暇唤醒生效）。';
        }
        assocSub.append(diagLine);
      }
      layerCard.append(assocSub);
    }

    // 2c. P6-S6-1 memory weights: activation, protective decay and versioned re-evaluation.
    const weightsRes = await reqJson(req, '/api/brain/memory-weights');
    if (weightsRes.ok && weightsRes.data?.available) {
      layerCard.append(buildMemoryWeightsPanel(req, host, weightsRes.data));
    }

    // 3. Memory Growth Analysis & One-Click Report
    const chartCard = el('article', 'veil-card');
    const chartHead = el('div', 'veil-card-head');
    chartHead.append(el('h3', '', '记忆增长分析'));
    const rangeSeg = el('div', 'seg-tabs');
    const rangeBtns = [
      { key: 'today', label: '今日' },
      { key: '7d', label: '近 7 天' },
      { key: '30d', label: '近 30 天' },
      { key: 'custom', label: '自定义' },
    ];
    let currentRange = '7d';
    let currentView = 'added_updated';
    let currentGrowth = gData || { summary: {}, days: [] };
    let selectedChartDate = '';

    const customWrap = el('div', 'inline-controls');
    customWrap.hidden = true;
    const fromInp = el('input', 'veil-input');
    fromInp.type = 'date';
    const toInp = el('input', 'veil-input');
    toInp.type = 'date';
    const applyCustom = btn('应用', 'sm');
    customWrap.append(fromInp, el('span', '', '至'), toInp, applyCustom);

    const viewSeg = el('div', 'seg-tabs');
    const viewDefs = [
      { key: 'added_updated', label: '新增与修订' },
      { key: 'cumulative', label: '累计与净增长' },
      { key: 'events', label: '事件新建与更新' },
      { key: 'types', label: '类型分布' },
    ];

    const legendBox = el('div', 'chart-legend');
    const chartFrame = el('div', 'growth-chart-frame');
    const axisY = el('div', 'growth-axis-y');
    const plotArea = el('div', 'growth-plot');
    const axisX = el('div', 'growth-axis-x');
    chartFrame.append(axisY, plotArea, axisX);

    const dayDrillBox = el('div', 'veil-subpanel');
    dayDrillBox.hidden = true;

    let dayChangeSeq = 0;
    const loadDayChanges = async dateStr => {
      const seq = ++dayChangeSeq;
      selectedChartDate = dateStr;
      drawGrowthChart();
      dayDrillBox.hidden = false;
      dayDrillBox.replaceChildren(el('p', 'muted', `正在加载 ${dateStr} 的实际记忆变更…`));
      const res = await reqJson(req, `/api/brain/day-changes?date=${encodeURIComponent(dateStr)}`);
      if (seq !== dayChangeSeq) return;
      if (!res.ok || !res.data) {
        dayDrillBox.replaceChildren(el('p', 'muted', '无法加载当日变更明细。'));
        return;
      }
      const head = el('div', 'veil-card-head');
      head.append(el('strong', '', `${dateStr} 实际变更明细（${res.data.changes.length} 条）`));
      const closeBtn = btn('收起', 'sm');
      closeBtn.addEventListener('click', () => {
        selectedChartDate = '';
        dayDrillBox.hidden = true;
        drawGrowthChart();
      });
      head.append(closeBtn);
      const list = el('div', 'stack-sm');
      if (!res.data.changes.length) {
        list.append(el('p', 'muted', '当天无实际记忆或事件内容变更。'));
      } else {
        for (const ch of res.data.changes.slice(0, 20)) {
          const row = el('div', 'flat-entry');
          row.append(
            el('strong', '', `${ch.kind_label || '变更'} · ${ch.title || ch.memory_id}`),
            el('small', 'muted', `${formatTime(ch.time)} · ${ch.summary || ''}`),
          );
          list.append(row);
        }
      }
      if (res.data.maintenance_logs && res.data.maintenance_logs.length) {
        const det = el('details');
        det.append(el('summary', '', `已折叠 ${res.data.maintenance_logs.length} 条无内容变更的系统维护日志`));
        for (const m of res.data.maintenance_logs) {
          det.append(el('div', 'muted', `${formatTime(m.time)} · ${m.operation} (${m.status})`));
        }
        list.append(det);
      }
      dayDrillBox.replaceChildren(head, list);
    };

    const mkLegendItem = (dotCls, text) => {
      const item = el('span', 'legend-item');
      item.append(el('span', `legend-dot ${dotCls}`), el('span', '', text));
      return item;
    };

    const drawGrowthChart = () => {
      const days = currentGrowth.days || [];
      const sum = currentGrowth.summary || {};
      legendBox.replaceChildren();
      axisY.replaceChildren();
      plotArea.replaceChildren();
      axisX.replaceChildren();

      if (currentView === 'types') {
        const byType = sum.by_type || {};
        const entries = Object.entries(byType).sort((a, b) => b[1] - a[1]);
        const totalCnt = entries.reduce((acc, [, v]) => acc + v, 0) || 1;
        legendBox.append(
          mkLegendItem('new', `记忆类型分布（共 ${entries.length} 类 · ${totalCnt} 条）`),
        );
        chartFrame.style.display = 'block';
        axisY.hidden = true;
        axisX.hidden = true;
        if (!entries.length) {
          plotArea.append(el('p', 'muted', '暂无记忆类型分布数据'));
          return;
        }
        const maxT = Math.max(1, ...entries.map(([, v]) => v));
        const list = el('div', 'type-dist-list');
        for (const [k, v] of entries) {
          const row = el('div', 'type-dist-row');
          const track = el('div', 'type-dist-track');
          const fill = el('div', 'type-dist-fill');
          fill.style.width = `${Math.max(4, Math.round((v / maxT) * 100))}%`;
          track.append(fill);
          const pct = Math.round((v / totalCnt) * 100);
          row.append(
            el('span', '', labelMemoryType(k)),
            track,
            el('span', 'type-dist-val', `${v} 条 (${pct}%)`),
          );
          list.append(row);
        }
        plotArea.append(list);
        return;
      }

      chartFrame.style.display = 'grid';
      axisY.hidden = false;
      axisX.hidden = false;

      if (currentView === 'cumulative') {
        legendBox.append(
          mkLegendItem('new', `活跃存量（当前 ${sum.total_active ?? 0} 条 · 累计创建 ${sum.total_created ?? sum.total_active ?? 0} 条）`),
          mkLegendItem('soft', `区间净增长 ${sum.net_growth >= 0 ? '+' : ''}${sum.net_growth ?? 0}`),
        );
        if (sum.history_incomplete && sum.history_incomplete_reason) {
          legendBox.append(pill(`历史不完整：${sum.history_incomplete_reason}`, 'warn'));
        }
        const vals = days.map(pt => pt.cumulative || 0);
        const minV = Math.max(0, Math.min(...vals, 0));
        const maxV = Math.max(minV + 1, ...vals);
        const midV = Math.round((minV + maxV) / 2);
        axisY.append(el('span', '', `${maxV}`), el('span', '', `${midV}`), el('span', '', `${minV}`));

        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNS, 'svg');
        svg.setAttribute('class', 'growth-line-svg');
        svg.setAttribute('viewBox', '0 0 600 164');
        svg.setAttribute('preserveAspectRatio', 'none');

        const n = Math.max(1, days.length);
        const coords = days.map((pt, idx) => {
          const x = n === 1 ? 300 : 28 + (idx / (n - 1)) * 544;
          const ratio = ((pt.cumulative || 0) - minV) / Math.max(1, maxV - minV);
          const y = 144 - ratio * 114;
          return { x, y, pt };
        });

        if (coords.length >= 2) {
          const areaD = `M ${coords[0].x} 152 ` + coords.map(c => `L ${c.x} ${c.y}`).join(' ') + ` L ${coords[coords.length - 1].x} 152 Z`;
          const areaPath = document.createElementNS(svgNS, 'path');
          areaPath.setAttribute('class', 'growth-line-area');
          areaPath.setAttribute('d', areaD);
          svg.appendChild(areaPath);

          const lineD = coords.map((c, i) => `${i === 0 ? 'M' : 'L'} ${c.x} ${c.y}`).join(' ');
          const linePath = document.createElementNS(svgNS, 'path');
          linePath.setAttribute('class', 'growth-line-stroke');
          linePath.setAttribute('d', lineD);
          svg.appendChild(linePath);
        }

        const labelStep = n > 14 ? Math.ceil(n / 7) : 1;
        coords.forEach(({ x, y, pt }, idx) => {
          const g = document.createElementNS(svgNS, 'g');
          g.setAttribute('class', 'growth-point-group');
          g.setAttribute('aria-selected', String(pt.date === selectedChartDate));
          g.addEventListener('click', () => loadDayChanges(pt.date));

          const dot = document.createElementNS(svgNS, 'circle');
          dot.setAttribute('class', 'growth-point-dot');
          dot.setAttribute('cx', String(x));
          dot.setAttribute('cy', String(y));
          dot.setAttribute('r', pt.date === selectedChartDate ? '6' : '4.5');
          g.appendChild(dot);

          if (idx % labelStep === 0 || idx === n - 1 || pt.date === selectedChartDate) {
            const txt = document.createElementNS(svgNS, 'text');
            txt.setAttribute('class', 'growth-point-label');
            txt.setAttribute('x', String(x));
            txt.setAttribute('y', String(Math.max(14, y - 10)));
            const netStr = pt.net_growth ? ` (${pt.net_growth >= 0 ? '+' : ''}${pt.net_growth})` : '';
            txt.textContent = `${pt.cumulative || 0}${n <= 10 ? netStr : ''}`;
            g.appendChild(txt);
          }

          const titleEl = document.createElementNS(svgNS, 'title');
          titleEl.textContent = `${pt.date} 累计 ${pt.cumulative || 0}（净增长 ${pt.net_growth >= 0 ? '+' : ''}${pt.net_growth || 0}）`;
          g.appendChild(titleEl);
          svg.appendChild(g);

          const xLbl = el('span', '', (idx % labelStep === 0 || idx === n - 1) ? pt.label : '');
          axisX.append(xLbl);
        });
        plotArea.append(svg);
        return;
      }

      if (currentView === 'events') {
        legendBox.append(
          mkLegendItem('new', `新建事件 (${sum.events_created ?? 0})`),
          mkLegendItem('revised', `事件更新 (${sum.events_updated ?? 0})`),
        );
      } else {
        legendBox.append(
          mkLegendItem('new', `新增记忆 (${sum.actual_added ?? 0})`),
          mkLegendItem('revised', `修订次数 (${sum.actual_updated ?? 0})`),
        );
      }

      const maxVal = Math.max(
        1,
        ...days.map(pt => (currentView === 'events'
          ? (pt.events_created || 0) + (pt.events_updated || 0)
          : (pt.added || 0) + (pt.updated || 0))),
      );
      axisY.append(el('span', '', `${maxVal}`), el('span', '', `${Math.round(maxVal / 2)}`), el('span', '', '0'));

      const barsWrap = el('div', 'growth-bars-wrap');
      const labelStep = days.length > 14 ? Math.ceil(days.length / 7) : 1;
      days.forEach((pt, idx) => {
        const col = el('div', 'growth-col');
        col.setAttribute('role', 'button');
        col.setAttribute('tabindex', '0');
        col.setAttribute('aria-selected', String(pt.date === selectedChartDate));
        col.dataset.date = pt.date;
        col.title = `点击查看 ${pt.date} 变更明细`;
        col.addEventListener('click', () => loadDayChanges(pt.date));
        col.addEventListener('keydown', e => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); loadDayChanges(pt.date); }
        });

        const v1 = currentView === 'events' ? (pt.events_created || 0) : (pt.added || 0);
        const v2 = currentView === 'events' ? (pt.events_updated || 0) : (pt.updated || 0);
        const totalDay = v1 + v2;
        if (totalDay > 0 || days.length <= 10) {
          col.append(el('span', 'bar-val-label', `${totalDay}`));
        }

        const stack = el('div', 'bar-stack');
        const h1 = Math.round((v1 / maxVal) * 118);
        const h2 = Math.round((v2 / maxVal) * 118);
        const segNew = el('div', 'bar-seg-new');
        segNew.style.height = `${h1}px`;
        segNew.title = currentView === 'events' ? `${pt.date} 新建事件 ${v1}` : `${pt.date} 新增 ${v1}`;
        const segRev = el('div', 'bar-seg-revised');
        segRev.style.height = `${h2}px`;
        segRev.title = currentView === 'events' ? `${pt.date} 更新事件 ${v2}` : `${pt.date} 修订 ${v2}`;
        stack.append(segNew, segRev);
        col.append(stack);
        barsWrap.append(col);

        axisX.append(el('span', '', (idx % labelStep === 0 || idx === days.length - 1) ? pt.label : ''));
      });
      plotArea.append(barsWrap);
    };

    let growthSeq = 0;
    const reloadGrowth = async (rangeKey, fDate = '', tDate = '') => {
      const seq = ++growthSeq;
      currentRange = rangeKey;
      for (const b of rangeSeg.children) {
        b.setAttribute('aria-pressed', String(b.dataset.range === rangeKey));
      }
      customWrap.hidden = rangeKey !== 'custom';
      const qs = new URLSearchParams({ range: rangeKey });
      if (fDate) qs.set('from_date', fDate);
      if (tDate) qs.set('to_date', tDate);
      const res = await reqJson(req, `/api/brain/growth?${qs.toString()}`);
      if (seq !== growthSeq) return;
      if (res.ok && res.data) {
        currentGrowth = res.data;
        drawGrowthChart();
      }
    };

    for (const rb of rangeBtns) {
      const b = el('button', '', rb.label);
      b.type = 'button';
      b.dataset.range = rb.key;
      b.setAttribute('aria-pressed', String(rb.key === currentRange));
      b.addEventListener('click', () => {
        if (rb.key === 'custom') {
          currentRange = 'custom';
          customWrap.hidden = false;
          for (const ch of rangeSeg.children) ch.setAttribute('aria-pressed', String(ch.dataset.range === 'custom'));
        } else {
          reloadGrowth(rb.key);
        }
      });
      rangeSeg.append(b);
    }
    applyCustom.addEventListener('click', () => reloadGrowth('custom', fromInp.value, toInp.value));
    chartHead.append(rangeSeg);

    for (const vd of viewDefs) {
      const b = el('button', '', vd.label);
      b.type = 'button';
      b.dataset.view = vd.key;
      b.setAttribute('aria-pressed', String(vd.key === currentView));
      b.addEventListener('click', () => {
        currentView = vd.key;
        for (const ch of viewSeg.children) ch.setAttribute('aria-pressed', String(ch.dataset.view === currentView));
        drawGrowthChart();
      });
      viewSeg.append(b);
    }

    // One-click Growth & Change Report controls
    const reportBar = el('div', 'inline-controls');
    const chkContent = el('input');
    chkContent.type = 'checkbox';
    chkContent.checked = true;
    const lblContent = el('label', 'inline-check');
    lblContent.append(chkContent, '包含记忆正文');
    const chkHealth = el('input');
    chkHealth.type = 'checkbox';
    const lblHealth = el('label', 'inline-check');
    lblHealth.append(chkHealth, '合并健康巡检');

    const copySummaryBtn = btn('复制摘要报告', 'sm');
    const copyFullBtn = btn('复制完整诊断报告', 'primary sm');
    const previewReportBtn = btn('预览报告', 'sm');
    previewReportBtn.hidden = true;
    const exportBtn = btn('导出报告', 'sm');
    const reportStatus = el('span', 'muted');
    let lastReportMarkdown = '';

    const fetchAndCopyReport = async (fmt, download = false) => {
      const s = currentGrowth.summary || {};
      const qs = new URLSearchParams({
        from_date: s.from_date || '',
        to_date: s.to_date || '',
        include_content: chkContent.checked ? '1' : '0',
        include_health: chkHealth.checked ? '1' : '0',
        format: fmt,
      });
      reportStatus.textContent = '生成中…';
      const res = await reqJson(req, `/api/brain/growth-report?${qs.toString()}`);
      if (!res.ok || !res.data?.markdown) {
        reportStatus.textContent = '生成失败';
        return;
      }
      lastReportMarkdown = res.data.markdown;
      previewReportBtn.hidden = false;
      if (download) {
        const blob = new Blob([res.data.markdown], { type: 'text/markdown;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `veil-brain-growth-${s.from_date || 'report'}.md`;
        a.click();
        reportStatus.textContent = '已导出报告';
      } else {
        const copied = await copyText(res.data.markdown);
        reportStatus.textContent = copied
          ? (fmt === 'summary' ? '已复制摘要报告 ✓' : '已复制完整报告 ✓')
          : '已生成（点击预览报告查看）';
      }
    };
    copySummaryBtn.addEventListener('click', () => fetchAndCopyReport('summary', false));
    copyFullBtn.addEventListener('click', () => fetchAndCopyReport('full', false));
    previewReportBtn.addEventListener('click', () => {
      if (!lastReportMarkdown) return;
      openBrainModal('记忆增长与变更诊断报告', '本地确定性统计 · 只读快照', body => {
        const copyInModal = btn('复制全文', 'sm primary');
        copyInModal.addEventListener('click', async () => {
          await copyText(lastReportMarkdown);
          copyInModal.textContent = '已复制 ✓';
        });
        body.append(copyInModal, el('pre', 'report-preview-box', lastReportMarkdown));
      });
    });
    exportBtn.addEventListener('click', () => fetchAndCopyReport('full', true));
    reportBar.append(lblContent, lblHealth, copySummaryBtn, copyFullBtn, previewReportBtn, exportBtn, reportStatus);

    drawGrowthChart();
    chartCard.append(chartHead, customWrap, viewSeg, legendBox, chartFrame, dayDrillBox, reportBar);

    // 4. Health Check Summary + On-Demand Modal Inspector (keeps overview compact)
    const healthCard = el('article', 'veil-card');
    const healthHead = el('div', 'veil-card-head');
    healthHead.append(el('h3', '', '健康巡检'));
    const healthActions = el('div', 'inline-controls');
    const expandHealthBtn = btn('展开完整巡检', 'sm');
    expandHealthBtn.addEventListener('click', () => {
      openBrainModal('记忆健康巡检中心', '严格只读核验 · 支持按日期范围检查与报告导出', async body => {
        await renderHealthWorkspace(body, req);
      });
    });
    healthActions.append(expandHealthBtn);
    healthHead.append(healthActions);
    const hSummaryText = hSum
      ? `最近巡检：${formatTime(data.health_scanned_at)} · 状态：${hSum.status_label || hSum.status || '完成'} · 异常 ${hSum.error_count || 0} / 关注 ${hSum.warn_count || 0}`
      : '尚未进行首次检查（点击“展开完整巡检”可执行只读巡检、查看历史报告与配置定期检查）';
    healthCard.append(healthHead, el('p', 'muted', hSummaryText));
    if (hSum) {
      const hPills = el('div', 'inline-controls');
      hPills.append(
        pill(`通过 ${hSum.good_count || 0} 项`, 'good'),
        pill(`需关注 ${(hSum.warn_count || 0) + (hSum.error_count || 0)} 项`, (hSum.warn_count || hSum.error_count) ? 'warn' : 'good'),
      );
      healthCard.append(hPills);
    }

    // 5. Recent Activities (Compact list; click to inspect readable before/after diff & copy report in modal)
    const actCard = el('article', 'veil-card');
    const tlHead = el('div', 'veil-card-head');
    tlHead.append(el('h3', '', '近期记忆与事件轨迹'));
    const tlBtn = btn('打开记忆档案', 'sm');
    tlBtn.addEventListener('click', () => window.veilWorkspace?.go('memory'));
    tlHead.append(tlBtn);
    actCard.append(tlHead);
    const acts = data.activities || [];
    if (!acts.length) {
      actCard.append(renderEmptyState('近期暂无写入动态', '新的对话整理和事件记录会按时间出现在这里。'));
    } else {
      const list = el('div', 'stack-sm');
      for (const act of acts.slice(0, 6)) {
        const row = el('button', 'index-item');
        row.type = 'button';
        const top = el('div', 'index-item-top');
        top.append(el('strong', '', act.title || act.kind_label || '记忆变更'), el('small', '', formatTime(act.time).slice(5, 16)));
        row.append(top, el('small', 'muted', act.meta || act.source || ''));
        row.addEventListener('click', () => {
          openBrainModal(act.title || '记忆变更详情', `来源：${act.source || '记忆系统'} · 时间：${formatTime(act.time)}`, (body, closeModal) => {
            if (act.detail) body.append(el('p', '', act.detail));
            if (act.content) body.append(el('div', 'reader-why', act.content));
            if (Array.isArray(act.diff) && act.diff.length) {
              body.append(el('strong', '', '版本变化对比'), buildReadableDiffNode(act.diff, '', act.content || ''));
            }
            const actBtns = el('div', 'inline-controls');
            const copyActBtn = btn('复制轨迹报告', 'sm primary');
            copyActBtn.addEventListener('click', async () => {
              const repLines = [
                `# 记忆轨迹报告 · ${act.title || ''}`,
                `- 类型：${act.kind_label || act.kind || '变更'}`,
                `- 来源：${act.source || '记忆系统'}`,
                `- 时间：${formatTime(act.time)}`,
                `- 说明：${act.detail || '无'}`,
                act.content ? `\n## 正文内容\n${act.content}` : '',
                Array.isArray(act.diff) && act.diff.length ? `\n## 版本差异\n\`\`\`diff\n${act.diff.join('\n')}\n\`\`\`` : '',
              ].filter(Boolean).join('\n');
              const ok = await copyText(repLines);
              copyActBtn.textContent = ok ? '已复制轨迹报告 ✓' : '复制失败';
            });
            actBtns.append(copyActBtn);
            if (act.event_id || act.memory_id) {
              const jumpBtn = btn(act.event_id ? '查看关联事件' : '定位到记忆档案', 'sm');
              jumpBtn.addEventListener('click', () => {
                closeModal();
                if (act.event_id) window.veilWorkspace?.openEvent(act.event_id);
                else if (act.memory_id) window.veilWorkspace?.openMemory(act.memory_id);
              });
              actBtns.append(jumpBtn);
            }
            body.append(actBtns);
          });
        });
        list.append(row);
      }
      actCard.append(list);
    }

    const bentoSplit = el('div', 'brain-home-bento');
    const bentoSide = el('div', 'brain-home-bento-side');
    bentoSide.append(healthCard, actCard);
    bentoSplit.append(chartCard, bentoSide);

    const wrap = el('div', 'brain-home-compact stack-sm');
    wrap.append(kpiGrid, layerCard, bentoSplit);
    host.replaceChildren(wrap);
  }

  // =========================================================================
  // 2. P4 Memory Library Add-on inside #memory-view (full search, diff, lock)
  // =========================================================================
  function mountMemoryLibraryAddon(req) {
    lastReq = req || lastReq;
    const view = document.getElementById('memory-view');
    if (!view || memoryAddonMounted) return;
    memoryAddonMounted = true;
    view.classList.add('p4-unified');

    const addon = el('section', 'brain-library-shell');
    addon.id = 'brain-library-addon';
    addon.style.marginTop = '10px';

    const head = el('div', 'veil-card-head');
    const titleWrap = el('div');
    titleWrap.append(el('h3', '', '记忆档案库'));
    const statsLabel = el('small', '', '支持关键词检索、状态筛选、历史版本对比与安全编辑');
    titleWrap.append(statsLabel);
    const refreshBtn = btn('刷新全库', 'sm primary');
    head.append(titleWrap, refreshBtn);

    const filterBar = el('div', 'filter-bar');
    const qInput = document.createElement('input');
    qInput.type = 'search';
    qInput.placeholder = '搜索标题、正文、为什么记住或 ID…';
    qInput.style.minWidth = '210px';
    qInput.style.flex = '1';

    const mkSelect = (opts) => {
      const sel = document.createElement('select');
      for (const [val, label] of opts) {
        const opt = el('option', '', label);
        opt.value = val;
        sel.append(opt);
      }
      return sel;
    };
    const statusSel = mkSelect([['active', '状态: 活跃'], ['archived', '状态: 已归档'], ['locked', '状态: 已锁定'], ['pending_vector', '状态: 待补向量'], ['all', '状态: 全部']]);
    const kindSel = mkSelect([['all', '类型: 全部'], ['pinned', '仅置顶'], ['event_link', '关联事件'], ['fragment', '短碎片'], ['proposal', '有待重评建议']]);
    const sortSel = mkSelect([['updated', '排序: 最近更新'], ['recalled', '排序: 召回频次'], ['importance', '排序: 重要度'], ['activity', '排序: 活跃度'], ['created', '排序: 创建时间']]);
    filterBar.append(qInput, statusSel, kindSel, sortSel);

    const split = el('div', 'split-reader');
    const listPane = el('div', 'index-pane');
    const readerPane = el('div', 'veil-card reader-pane');
    readerPane.append(renderEmptyState('在左侧选择一条记忆', '可查看为什么记住、来源事件、版本 Diff，或进行安全编辑与归档。'));

    const pager = el('div', 'workspace-links');
    const prevBtn = btn('上一页', 'sm');
    const nextBtn = btn('下一页', 'sm');
    const pageStatus = el('small', '', '');
    pager.append(prevBtn, pageStatus, nextBtn);

    split.append(listPane, readerPane);
    addon.append(head, filterBar, split, pager);
    view.append(addon);

    let state = { offset: 0, limit: 30, selectedId: null, items: [], filtered: 0, listSeq: 0, detailSeq: 0 };
    const drafts = new Map();

    function openDetail(item) {
      state.selectedId = item.memory_id;
      for (const node of listPane.querySelectorAll('.index-item')) {
        node.setAttribute('aria-current', String(node.dataset.memId === item.memory_id));
      }
      readerPane.replaceChildren();

      const badgeRow = el('div', 'index-tags');
      badgeRow.append(pill(labelMemoryType(item.kind), 'rose'), pill(item.bucket === 'archive' ? '已归档' : (item.bucket === 'core' ? '核心' : '动态')));
      if (item.pinned) badgeRow.append(pill('置顶', 'gold'));
      if (item.locked) badgeRow.append(pill('锁定', 'warn'));
      if (item.archived) badgeRow.append(pill('已归档'));
      if (item.vector_state && item.vector_state !== 'ready') badgeRow.append(pill('待补索引', 'warn'));
      if (item.reeval && item.reeval.pending) badgeRow.append(pill('待重评', 'warn'));

      const title = el('h3', 'reader-title', item.title);
      const act = item.activation || {};
      const meta = el('div', 'reader-meta-grid');
      meta.append(
        el('span', '', `编号: ${item.memory_id}`),
        el('span', '', `重要度: ${item.importance ?? '—'} / 10`),
        el('span', '', `更新: ${formatTime(item.updated_at)}`),
        el('span', '', `召回: ${item.recalled ?? 0} 次`),
        el('span', '', `版本: ${item.version_count ?? 0}`),
        el('span', '', `校验: ${shortSha(item.sha256)}`),
        el('span', '', `最近活跃: ${item.last_active ? formatTime(item.last_active) : '—'}`),
        el('span', '', `活跃度: ${act.display ?? 0}% · 送达 ${act.deliveries ?? 0} 次 · 跨 ${act.distinct_days ?? 0} 天 / ${act.distinct_sessions ?? 0} 会话`),
        el('span', '', act.protected ? '保护: 重要或置顶，永不衰减' : (act.decayed ? '活跃度: 已按半衰期衰减' : '活跃度: 未衰减')),
      );

      readerPane.append(badgeRow, title, meta);
      if (item.why) readerPane.append(el('div', 'reader-why', `为什么记住：${item.why}`));

      // Keep the server text and the unsaved draft separate: after a 409 the user must be able
      // to compare them and decide, never have the draft silently replaced or auto-retried.
      const serverBody = item.body_exact ?? item.body ?? '';
      const draft = drafts.get(item.memory_id);
      const bodyEditor = document.createElement('textarea');
      bodyEditor.rows = 8;
      bodyEditor.style.width = '100%';
      bodyEditor.value = draft !== undefined ? draft : serverBody;
      bodyEditor.addEventListener('input', () => drafts.set(item.memory_id, bodyEditor.value));

      const actionStatus = el('p', '', '');
      const draftArea = el('div', '');
      if (draft !== undefined) {
        const box = el('div', 'veil-subpanel');
        box.append(el('p', '', '你有未保存的草稿（基于旧版本）。服务器上的最新版本如下，核对后再决定保存或丢弃。'));
        box.append(el('pre', 'diff-box', serverBody || '（服务器版本为空）'));
        const discard = btn('丢弃我的草稿，改用服务器版本', 'sm');
        discard.addEventListener('click', () => {
          drafts.delete(item.memory_id);
          openDetail(item);
        });
        box.append(discard);
        draftArea.append(box);
      }
      const actions = el('div', 'workspace-links');
      const saveBtn = btn('保存修改', 'sm primary');
      const verBtn = btn('查看历史版本', 'sm');
      const archBtn = btn(item.archived ? '恢复到活跃记忆' : '移入归档', 'sm');
      const delBtn = btn('申请删除', 'sm danger');

      const extraArea = el('div', '');

      saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true;
        const submittedDraft = bodyEditor.value;
        actionStatus.textContent = '正在校验 SHA-256 并保存…';
        try {
          const res = await reqJson(lastReq, '/api/memory/edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id: item.memory_id,
              // panel_edit only reads body['changes']; the top-level content used to be dropped.
              changes: { content: submittedDraft },
              expected_sha256: item.sha256 || '',
            }),
          });
          if (res.status === 409) {
            actionStatus.textContent = '并发冲突 (409)：后台已更新过此记忆。你的草稿仍保留，点击“刷新全库”可对照服务器最新版本后再决定。';
            return;
          }
          if (!res.ok) {
            actionStatus.textContent = res.data?.error || '保存失败。';
            return;
          }
          // If the user kept typing while the request was in flight, that newer draft is theirs.
          if (drafts.get(item.memory_id) === submittedDraft) drafts.delete(item.memory_id);
          actionStatus.textContent = '保存成功！';
          await loadLibrary(item.memory_id);
        } catch (err) {
          actionStatus.textContent = err?.message || '保存失败，草稿仍保留。';
        } finally {
          saveBtn.disabled = false;
        }
      });

      verBtn.addEventListener('click', async () => {
        verBtn.disabled = true;
        try {
          const res = await reqJson(lastReq, '/api/memory/versions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: item.memory_id }),
          });
          extraArea.replaceChildren();
          if (!res.ok) {
            extraArea.append(el('p', '', res.data?.error || '版本读取失败。'));
            return;
          }
          const versions = res.data?.versions || [];
          if (!versions.length) {
            extraArea.append(el('p', '', '暂无历史版本快照。'));
            return;
          }
          for (const v of versions) {
            const entry = el('div', 'flat-entry');
            entry.append(el('strong', '', `${formatTime(v.time)} · ${v.operation_reason || '编辑'}`));
            if (v.diff) entry.append(buildReadableDiffNode(v.diff, v.old_content, serverBody));
            else if (v.old_content) entry.append(el('div', 'diff-box', v.old_content.slice(0, 400)));
            extraArea.append(entry);
          }
        } catch (err) {
          extraArea.replaceChildren(el('p', '', err?.message || '版本读取失败。'));
        } finally {
          verBtn.disabled = false;
        }
      });

      archBtn.addEventListener('click', async () => {
        archBtn.disabled = true;
        try {
          // /api/memory/archived only lists; the reversible state change is /api/memory/archive.
          const res = await reqJson(lastReq, item.archived ? '/api/memory/unarchive' : '/api/memory/archive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: item.memory_id }),
          });
          if (!res.ok) {
            actionStatus.textContent = res.data?.error || '归档状态切换失败。';
            return;
          }
          actionStatus.textContent = res.data?.message || '';
          await loadLibrary(item.memory_id);
        } catch (err) {
          actionStatus.textContent = err?.message || '归档状态切换失败。';
        } finally {
          archBtn.disabled = false;
        }
      });

      delBtn.addEventListener('click', async () => {
        if (!window.confirm(`确定要对记忆「${item.title}」提交删除请求吗？`)) return;
        const res = await reqJson(lastReq, '/api/memory/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: item.memory_id }),
        });
        if (res.ok) {
          if (state.selectedId === item.memory_id) {
            state.detailSeq += 1;
            state.selectedId = null;
            readerPane.replaceChildren(renderEmptyState('在左侧选择一条记忆', '这条记忆已提交删除，详情不再可编辑。'));
          }
          await loadLibrary();
        } else {
          actionStatus.textContent = res.data?.error || '删除失败或已被保护。';
        }
      });

      actions.append(saveBtn, verBtn, archBtn, delBtn);

      if (item.event?.event_id) {
        const evJump = btn(`关联事件：${item.event.title}`, 'sm');
        evJump.addEventListener('click', () => window.veilWorkspace?.openEvent(item.event.event_id));
        actions.append(evJump);
      }

      // P6-S6-1: activation + versioned re-evaluation live next to the existing edit/version tools.
      const reevalArea = el('div', '');
      const reevalActions = el('div', 'workspace-links');
      const reevaluateBtn = btn('重新评估长期重要度', 'sm');
      const historyBtn = btn('查看重评历史', 'sm');
      reevalActions.append(reevaluateBtn, historyBtn);
      reevalArea.append(reevalActions);
      const pendingProposal = item.reeval && item.reeval.pending;
      if (pendingProposal) {
        const p = pendingProposal;
        const card = el('div', 'veil-subpanel');
        card.append(el('strong', '', `待你确认的重评：重要度 ${p.current_importance} → ${p.proposed_importance}`));
        card.append(el('small', 'muted', `置信度 ${Math.round((p.confidence || 0) * 100)}% · 策略 ${p.policy_version || ''}`));
        const ul = el('ul', 'flat-list');
        for (const r of p.reasons || []) ul.append(el('li', '', (r.label || r.code) + '：' + (r.detail || '')));
        card.append(ul);
        const line = el('small', 'muted', '');
        const accept = btn('接受并写入版本', 'sm primary');
        const reject = btn('拒绝', 'sm');
        accept.addEventListener('click', async () => {
          accept.disabled = true;
          const out = await reqJson(req, '/api/brain/memory-proposal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposal_id: p.proposal_id, action: 'accept' }),
          });
          accept.disabled = false;
          if (out.ok && out.data?.ok) {
            await loadLibrary(item.memory_id);
          } else {
            line.textContent = out.data?.error || '应用失败。';
          }
        });
        reject.addEventListener('click', async () => {
          reject.disabled = true;
          const out = await reqJson(req, '/api/brain/memory-proposal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposal_id: p.proposal_id, action: 'reject' }),
          });
          reject.disabled = false;
          if (out.ok && out.data?.ok) {
            await loadLibrary(item.memory_id);
          } else {
            line.textContent = out.data?.error || '拒绝失败。';
          }
        });
        card.append(accept, reject, line);
        reevalArea.append(card);
      }
      reevaluateBtn.addEventListener('click', async () => {
        reevaluateBtn.disabled = true;
        const out = await reqJson(req, '/api/brain/memory-reevaluate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: item.memory_id }),
        });
        reevaluateBtn.disabled = false;
        if (out.ok && out.data?.ok) {
          await loadLibrary(item.memory_id);
        } else {
          const reasons = {
            no_change: '现有证据不足以调整长期重要度。',
            cooldown: '距离上次重评还不到冷却时间。',
            pending_exists: '已有一条待你确认的建议。',
            no_new_evidence: '没有出现新的证据。',
            memory_missing: '这条记忆不存在或已删除。',
            reeval_disabled: '动态重评已关闭。',
          };
          actionStatus.textContent = reasons[out.data?.reason] || out.data?.error || '本次没有可调整的长期重要度证据。';
        }
      });
      historyBtn.addEventListener('click', async () => {
        const old = reevalArea.querySelector('.reeval-history');
        if (old) { old.remove(); return; }
        const box = await renderMemoryReevalHistory(req, item, () => loadLibrary(item.memory_id));
        box.classList.add('reeval-history');
        reevalArea.append(box);
      });
      readerPane.append(bodyEditor, draftArea, actions, actionStatus, reevalArea, extraArea);
    }

    async function loadLibrary(focusId) {
      if (focusId) state.selectedId = focusId;
      // Snapshot the query and a sequence number: a slower response for an older query must not
      // overwrite a newer filter's list, page number or error state.
      const seq = ++state.listSeq;
      const qs = new URLSearchParams({
        q: qInput.value.trim(),
        status: statusSel.value,
        kind: kindSel.value,
        sort: sortSel.value,
        offset: String(state.offset),
        limit: String(state.limit),
      });
      const { ok, data } = await reqJson(lastReq, `/api/memory/library?${qs}`);
      if (seq !== state.listSeq) return;
      if (!ok || !data) {
        listPane.replaceChildren(el('p', '', '暂时无法加载全量记忆库。'));
        return;
      }
      state.items = data.items || [];
      state.filtered = data.filtered || 0;
      statsLabel.textContent = `总计 ${data.total ?? 0} 条，当前筛选命中 ${state.filtered} 条`;
      pageStatus.textContent = `第 ${Math.floor(state.offset / state.limit) + 1} 页 · 共 ${Math.max(1, Math.ceil(state.filtered / state.limit))} 页`;
      prevBtn.disabled = state.offset <= 0;
      nextBtn.disabled = state.offset + state.limit >= state.filtered;

      listPane.replaceChildren();
      if (!state.items.length) {
        listPane.append(renderEmptyState('无匹配记忆', '请放宽搜索词或筛选状态。'));
        return;
      }
      if (!state.selectedId || !state.items.some(it => it.memory_id === state.selectedId)) {
        state.selectedId = state.items[0].memory_id;
      }
      for (const item of state.items) {
        const card = el('button', 'index-item');
        card.type = 'button';
        card.dataset.memId = item.memory_id;
        card.setAttribute('aria-current', String(item.memory_id === state.selectedId));
        const top = el('div', 'index-item-top');
        top.append(el('strong', '', item.title), el('small', '', formatTime(item.updated_at).slice(0, 10)));
        const tags = el('div', 'index-tags');
        tags.append(pill(labelMemoryType(item.kind)));
        if (item.pinned) tags.append(pill('置顶', 'gold'));
        if (item.recalled) tags.append(pill(`召回 ${item.recalled}`));
        if (item.event?.title) tags.append(pill('关联事件', 'rose'));
        card.append(top, el('div', 'index-preview', item.body || item.why || ''), tags);
        card.addEventListener('click', () => { state.detailSeq += 1; openDetail(item); });
        listPane.append(card);
        if (state.selectedId === item.memory_id) openDetail(item);
      }
    }

    async function openMemoryById(memId) {
      if (!memId) return;
      // Opening one memory must not invalidate the list load that mounted this add-on.
      const seq = ++state.detailSeq;
      const qs = new URLSearchParams({ q: String(memId), status: 'all', kind: 'all',
                                       sort: 'updated', offset: '0', limit: '30' });
      const { ok, data } = await reqJson(lastReq, `/api/memory/library?${qs}`);
      if (seq !== state.detailSeq || !ok || !data) return;
      const item = (data.items || []).find(row => row.memory_id === memId);
      if (!item) return;
      state.selectedId = memId;
      for (const node of listPane.querySelectorAll('.index-item')) {
        node.setAttribute('aria-current', String(node.dataset.memId === memId));
      }
      openDetail(item);
    }

    refreshBtn.addEventListener('click', () => loadLibrary());
    for (const ctrl of [statusSel, kindSel, sortSel]) {
      ctrl.addEventListener('change', () => { state.offset = 0; loadLibrary(); });
    }
    qInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') { state.offset = 0; loadLibrary(); }
    });
    prevBtn.addEventListener('click', () => { state.offset = Math.max(0, state.offset - state.limit); loadLibrary(); });
    nextBtn.addEventListener('click', () => { state.offset += state.limit; loadLibrary(); });

    addon._loadLibrary = loadLibrary;
    addon._openMemoryById = openMemoryById;
    loadLibrary();
  }

  // =========================================================================
  // 3. P4 Event Stories (workspace-events)
  // =========================================================================
  async function renderEventsWorkspace(host, req, focusEventId = null) {
    const renderSeq = ++eventsRenderSeq;
    host.replaceChildren(el('p', '', '正在加载共同经历与事件故事…'));
    const { ok, data } = await reqJson(req, '/api/memory/events');
    if (renderSeq !== eventsRenderSeq) return;  // a newer navigation already owns the view
    const events = data?.events || data?.items || [];
    if (!ok || !Array.isArray(events) || !events.length) {
      host.replaceChildren(renderEmptyState('暂无事件故事', '共同经历会在自动梳理后按时间线沉淀在这里。'));
      return;
    }

    const filterInput = document.createElement('input');
    filterInput.type = 'search';
    filterInput.placeholder = '按事件标题或关键字筛选…';
    filterInput.style.marginBottom = '10px';
    filterInput.style.width = 'min(360px, 100%)';

    const split = el('div', 'split-reader');
    const listPane = el('div', 'index-pane');
    const readerPane = el('article', 'veil-card reader-pane');
    readerPane.append(renderEmptyState('在左侧选择一个事件', '查看完整故事脉络、时间阶段、关联记忆卡与事件整理操作。'));

    let selectedId = focusEventId || events[0]?.event_id || events[0]?.id;
    let detailSeq = 0;

    async function loadEventDetail(evId) {
      selectedId = evId;
      const seq = ++detailSeq;
      for (const n of listPane.querySelectorAll('.index-item')) {
        n.setAttribute('aria-current', String(n.dataset.evId === evId));
      }
      readerPane.replaceChildren(el('p', '', '正在读取事件详情…'));
      const res = await reqJson(req, `/api/memory/event?id=${encodeURIComponent(evId)}`);
      if (seq !== detailSeq) return;  // a newer selection already owns the pane
      if (!res.ok || !res.data) {
        readerPane.replaceChildren(el('p', '', '读取事件详情失败。'));
        return;
      }
      const ev = res.data.event || res.data;
      readerPane.replaceChildren();

      const tags = el('div', 'index-tags');
      if (ev.status) tags.append(pill(ev.status, 'rose'));
      if (ev.pending_review) tags.append(pill('待审核冲突', 'warn'));
      if (ev.story_pending) tags.append(pill('待重整故事', 'gold'));

      readerPane.append(
        tags,
        el('h3', 'reader-title', ev.title || evId),
        el('div', 'reader-meta-grid', `事件ID: ${evId} · 时间: ${formatTime(ev.first_at)} ~ ${formatTime(ev.last_at)}`),
        el('div', 'reader-prose', ev.story || ev.summary || ev.body || '（暂无事件正文）'),
      );

      const linked = res.data.linked_memories || ev.linked_memories || [];
      if (linked.length) {
        const linkCard = el('div', 'veil-card');
        linkCard.style.marginBottom = '12px';
        linkCard.append(el('h4', '', `关联记忆卡片 (${linked.length})`));
        const chipRow = el('div', 'workspace-links');
        for (const mem of linked) {
          const jump = btn(mem.title || mem.memory_id, 'sm');
          jump.addEventListener('click', () => window.veilWorkspace?.openMemory(mem.memory_id));
          chipRow.append(jump);
        }
        linkCard.append(chipRow);
        readerPane.append(linkCard);
      }

      const stages = res.data.stages || ev.stages || [];
      if (stages.length) {
        const stHead = el('h4', '', `阶段与片段 (${stages.length})`);
        const tl = el('ul', 'veil-timeline');
        for (const st of stages) {
          const li = el('li', 'tl-item');
          li.append(el('span', 'tl-dot gold'));
          const card = el('div', 'tl-card');
          card.append(
            el('div', 'tl-title', st.stage_title || st.at || '阶段'),
            el('div', 'tl-detail', st.summary || st.note || st.content || ''),
          );
          li.append(card);
          tl.append(li);
        }
        readerPane.append(stHead, tl);
      }
    }

    function drawList(q = '') {
      const term = q.trim().toLowerCase();
      listPane.replaceChildren();
      for (const ev of events) {
        const evId = ev.event_id || ev.id;
        const title = ev.title || evId;
        if (term && !`${title} ${ev.summary || ''}`.toLowerCase().includes(term)) continue;
        const item = el('button', 'index-item');
        item.type = 'button';
        item.dataset.evId = evId;
        item.setAttribute('aria-current', String(evId === selectedId));
        item.append(
          el('div', 'index-item-top', ''),
          el('div', 'index-preview', ev.summary || ev.story || ''),
        );
        item.firstChild.append(el('strong', '', title), el('small', '', formatTime(ev.last_at || ev.first_at).slice(0, 10)));
        item.addEventListener('click', () => loadEventDetail(evId));
        listPane.append(item);
      }
    }

    filterInput.addEventListener('input', () => drawList(filterInput.value));
    drawList();
    split.append(listPane, readerPane);
    host.replaceChildren(filterInput, split);
    if (selectedId) await loadEventDetail(selectedId);
  }

  // =========================================================================
  // 4. Relationship & Cognition (brain-cognition)
  // =========================================================================
  async function renderCognitionWorkspace(host, req) {
    host.replaceChildren(el('p', '', '正在加载关系底座与习得认知…'));
    const [pageRes, verRes] = await Promise.all([
      reqJson(req, '/api/peter-page'),
      reqJson(req, '/api/peter-page/versions'),
    ]);
    const page = pageRes.data || {};
    const learned = page.learned || {};
    const versions = verRes.data?.items || verRes.data?.versions || [];

    const utf8Bytes = str => new Blob([String(str || '')]).size;
    const relItems = (learned.relationship && learned.relationship.items) || [];
    const ongoingItems = (learned.ongoing && learned.ongoing.items) || [];
    const factsText = (learned.sections && learned.sections.facts && learned.sections.facts.text) || '';
    const growthText = (learned.sections && learned.sections.growth && learned.sections.growth.text) || '';

    const activeRel = relItems.filter(it => !it.stale && it.status !== 'outdated');
    const staleRel = relItems.filter(it => it.stale || it.status === 'outdated');
    const openOngoing = ongoingItems.filter(it => !['done', 'completed', 'closed', 'archived'].includes(it.status || 'active'));
    const closedOngoing = ongoingItems.filter(it => ['done', 'completed', 'closed', 'archived'].includes(it.status || 'active'));

    const usedBytes = (learned.budget && typeof learned.budget.used_bytes === 'number')
      ? learned.budget.used_bytes
      : (utf8Bytes(growthText) +
        utf8Bytes(factsText) +
        openOngoing.reduce((s, it) => s + utf8Bytes(it.text), 0) +
        activeRel.reduce((s, it) => s + utf8Bytes(it.text), 0));
    const maxBytes = (learned.budget && typeof learned.budget.max_bytes === 'number')
      ? learned.budget.max_bytes
      : (learned.max_bytes || 4000);
    const pct = Math.min(100, Math.round((usedBytes / Math.max(1, maxBytes)) * 100));

    // Shared budget summary card
    const budgetCard = el('article', 'veil-card');
    const bHead = el('div', 'veil-card-head');
    bHead.append(el('h3', '', '关系底座与习得认知 · 四区独立预算'), pill(`${usedBytes} / ${maxBytes} B (${pct}%)`, pct > 90 ? 'warn' : 'good'));
    const meter = el('div', 'budget-meter');
    const fill = el('div');
    fill.style.width = `${pct}%`;
    fill.style.background = pct > 90 ? 'var(--bad, #c0392b)' : 'var(--veil-accent)';
    meter.append(fill);
    const bMeta = el('div', 'inline-controls');
    const fullProfileBtn = btn('在 Peter 档案中编辑', 'sm');
    fullProfileBtn.addEventListener('click', () => window.veilWorkspace?.go('peter'));
    bMeta.append(el('span', 'muted', `最后更新：${formatTime(learned.updated_at || (learned.relationship && learned.relationship.updated_at)) || '未知'}`), fullProfileBtn);
    const bSections = el('div', 'layer-list');
    for (const [key, label] of [['facts', '习得认知'], ['growth', '成长记录'], ['relationship', '关系底座'], ['ongoing', '进行中事项']]) {
      const budget = learned.budgets?.sections?.[key];
      if (!budget) continue;
      const row = el('div', 'layer-item');
      row.append(el('strong', '', label), el('small', '',
        `${budget.used_bytes} / ${budget.max_bytes} B${budget.degraded ? '（超限，待整理）' : ''}`));
      bSections.append(row);
    }
    budgetCard.append(bHead, meter, bSections, bMeta);

    const grid = el('div', 'cognition-grid');

    // Relationship Base
    const relCard = el('article', 'veil-card');
    relCard.append(el('div', 'veil-card-head'));
    relCard.firstChild.append(el('h3', '', '关系底座'), pill(`${activeRel.length} 条有效`));
    if (!activeRel.length) {
      relCard.append(el('p', 'muted', '暂无有效关系底座记录。'));
    } else {
      const list = el('div', 'stack-sm');
      for (const it of activeRel) {
        const row = el('div', 'layer-item');
        row.append(el('div', 'reader-prose', it.text || ''));
        if (it.updated_at || it.source || it.locked || it.stale_ref || it.evidence_degraded || it.status) {
          const srcLabel = ({ lan: '你确认的', evidence: '有来源可查', peter: '角色自述' })[it.source] || it.source || '';
          const status = ({ confirmed: '已确认', uncertain: '未确认' })[it.status];
          const badges = [formatTime(it.updated_at), srcLabel, status, it.locked && '已锁定',
            it.evidence_degraded ? '证据失效已降级' : it.stale_ref && '部分引用失效'].filter(Boolean);
          row.append(el('small', 'muted', badges.join(' · ')));
          if (it.sources?.length) row.append(el('small', 'muted', `证据：${it.sources.join('、')}`));
        }
        list.append(row);
      }
      relCard.append(list);
    }
    if (staleRel.length) {
      const det = el('details');
      det.append(el('summary', '', `历史/过时关系记录 (${staleRel.length})`));
      for (const it of staleRel) det.append(el('p', 'muted', it.text || ''));
      relCard.append(det);
    }

    // Learned Context (Facts)
    const factsCard = el('article', 'veil-card');
    factsCard.append(el('div', 'veil-card-head'));
    factsCard.firstChild.append(el('h3', '', '习得认知'), pill(`${utf8Bytes(factsText)} B`));
    factsCard.append(el('div', 'reader-prose', factsText.trim() || '暂无习得认知记录。'));

    // Ongoing
    const ongoingCard = el('article', 'veil-card');
    ongoingCard.append(el('div', 'veil-card-head'));
    ongoingCard.firstChild.append(el('h3', '', '进行中事项'), pill(`${openOngoing.length} 项`));
    if (!openOngoing.length) {
      ongoingCard.append(el('p', 'muted', '暂无进行中事项。'));
    } else {
      const list = el('div', 'stack-sm');
      for (const it of openOngoing) {
        const row = el('div', 'layer-item');
        row.append(el('div', 'reader-prose', it.text || ''));
        list.append(row);
      }
      ongoingCard.append(list);
    }
    if (closedOngoing.length) {
      const det = el('details');
      det.append(el('summary', '', `已结束事项 (${closedOngoing.length})`));
      for (const it of closedOngoing) det.append(el('p', 'muted', it.text || ''));
      ongoingCard.append(det);
    }

    // Growth
    const growthCard = el('article', 'veil-card');
    growthCard.append(el('div', 'veil-card-head'));
    growthCard.firstChild.append(el('h3', '', '成长记录'), pill(`${utf8Bytes(growthText)} B`));
    growthCard.append(el('div', 'reader-prose', growthText.trim() || '暂无成长记录。'));

    grid.append(relCard, factsCard, ongoingCard, growthCard);

    // Versions & Technical Details (collapsed)
    const techCard = el('article', 'veil-card');
    const techDet = el('details');
    techDet.append(el('summary', '', `认知版本历史 (${versions.length}) 与技术结构`));
    if (versions.length) {
      for (const v of versions.slice(0, 8)) {
        const box = el('div', 'layer-item');
        box.append(el('strong', '', `${formatTime(v.time)} · ${v.reason || v.action || '自动更新'}`));
        if (v.diff) box.append(buildLineDiffNode(v.diff));
        techDet.append(box);
      }
    } else {
      techDet.append(el('p', 'muted', '暂无认知版本变更记录。'));
    }
    techCard.append(techDet);

    host.replaceChildren(budgetCard, grid, techCard);
  }

  // =========================================================================
  // 5. Recall & Injection Diagnostics (brain-recall)
  // =========================================================================
  async function renderRecallWorkspace(host, req, focusTurnId = null) {
    const renderSeq = ++recallRenderSeq;
    host.replaceChildren(el('p', '', '正在读取记忆召回链路与开销快照…'));

    let page = 1;
    let filterDate = '';
    let filterPurpose = '';
    let filterModel = '';

    const filterBar = el('div', 'inline-controls');
    const dateInp = el('input', 'veil-input');
    dateInp.type = 'date';
    const purpSel = el('select', 'veil-input');
    for (const opt of [['', '全部用途'], ['chat', '日常对话'], ['experience', '经历整理'], ['judge', '想起判官']]) {
      const o = el('option', '', opt[1]);
      o.value = opt[0];
      purpSel.append(o);
    }
    const modelInp = el('input', 'veil-input');
    modelInp.placeholder = '筛选模型…';
    const searchBtn = btn('筛选', 'sm');
    filterBar.append(dateInp, purpSel, modelInp, searchBtn);

    const trendCard = el('article', 'veil-card');
    const split = el('div', 'split-reader');
    const listPane = el('div', 'index-pane');
    const detailPane = el('article', 'veil-card reader-pane');
    const pager = el('div', 'inline-controls');
    let selectedId = focusTurnId || '';
    let detailSeq = 0;
    let turnsSeq = 0;

    async function loadTurns() {
      const seq = ++turnsSeq;
      const qs = new URLSearchParams({ page: String(page), limit: '20' });
      if (filterDate) qs.set('date', filterDate);
      if (filterPurpose) qs.set('purpose', filterPurpose);
      if (filterModel) qs.set('model', filterModel);
      const res = await reqJson(req, `/api/brain/recall-turns?${qs.toString()}`);
      if (seq !== turnsSeq || renderSeq !== recallRenderSeq) return;
      const items = res.data?.items || [];
      const total = res.data?.total || 0;
      const trends = res.data?.trends || [];

      if (trends.length) {
        const lastT = trends[trends.length - 1];
        const totalTurns = trends.reduce((s, t) => s + t.turns, 0);
        const totalMemTok = trends.reduce((s, t) => s + (t.mem_tokens_sum || 0), 0);
        const head = el('div', 'veil-card-head');
        head.append(
          el('h3', '', '历史召回与注入趋势'),
          el('small', '', `近 ${trends.length} 天累计 ${totalTurns} 轮 · 记忆注入 ${totalMemTok} tokens · 最近一日关系底座命中 ${lastT.base_hits} 次 / 自然联想 ${lastT.assoc_hits} 次`),
        );
        const maxTurns = Math.max(1, ...trends.map(t => t.turns || 0));
        const bars = el('div', 'recall-trend-bars');
        const lbls = el('div', 'recall-trend-labels');
        for (const t of trends) {
          const col = el('div', 'recall-trend-col');
          col.title = `${t.date}：${t.turns} 轮，关系底座 ${t.base_hits}，自然联想 ${t.assoc_hits}，记忆注入 ${t.mem_tokens_sum || 0} tokens`;
          col.append(el('span', 'bar-val-label', `${t.turns}`));
          const bar = el('div', 'recall-trend-bar');
          bar.style.height = `${Math.max(8, Math.round(((t.turns || 0) / maxTurns) * 64))}px`;
          col.append(bar);
          bars.append(col);
          lbls.append(el('span', '', (t.date || '').slice(5).replace('-', '/')));
        }
        trendCard.replaceChildren(head, bars, lbls);
      } else {
        trendCard.replaceChildren(el('h3', '', '历史召回与注入趋势'), el('p', 'muted', '暂无足够历史轮次数据'));
      }

      listPane.replaceChildren();
      if (!items.length) {
        listPane.append(renderEmptyState('暂无匹配的召回轮次', '调整筛选条件或完成一轮对话后查看。'));
        detailPane.replaceChildren();
        return;
      }

      if (!selectedId || !items.some(it => it.request_id === selectedId)) {
        selectedId = items[0].request_id;
      }
      for (const it of items) {
        const row = el('button', 'index-item');
        row.type = 'button';
        row.dataset.turnId = it.request_id;
        row.setAttribute('aria-current', String(it.request_id === selectedId));
        row.append(
          el('div', 'index-title', it.summary || '对话轮次'),
          el('div', 'index-meta', `${formatTime(it.time)} · ${it.model} · ${it.purpose_label || it.purpose}`),
        );
        row.addEventListener('click', () => showTurn(it));
        listPane.append(row);
      }

      pager.replaceChildren();
      const prevBtn = btn('上一页', 'sm');
      prevBtn.disabled = page <= 1;
      prevBtn.addEventListener('click', () => { page = Math.max(1, page - 1); loadTurns(); });
      const nextBtn = btn('下一页', 'sm');
      nextBtn.disabled = page * 20 >= total;
      nextBtn.addEventListener('click', () => { page += 1; loadTurns(); });
      const covNote = res.data?.coverage_note ? ` · ${res.data.coverage_note}` : '';
      pager.append(prevBtn, el('span', 'muted', `第 ${page} 页 / 共 ${total} 轮${covNote}`), nextBtn);

      const activeItem = items.find(it => it.request_id === selectedId) || items[0];
      if (activeItem) await showTurn(activeItem);
    }

    searchBtn.addEventListener('click', () => {
      page = 1;
      filterDate = dateInp.value;
      filterPurpose = purpSel.value;
      filterModel = modelInp.value.trim();
      loadTurns();
    });

    async function showTurn(item) {
      const tid = String(item.request_id || item.id || '');
      selectedId = tid;
      const seq = ++detailSeq;
      for (const n of listPane.querySelectorAll('.index-item')) {
        n.setAttribute('aria-current', String(n.dataset.turnId === tid));
      }
      detailPane.replaceChildren(el('p', '', '正在加载该轮召回层级详情…'));

      const tk = item.tokens || {};
      const head = el('div', 'veil-card-head');
      head.append(el('h3', '', item.summary || '单轮召回与注入分析'));
      const headActions = el('div', 'inline-controls');
      const copyBtn = btn('复制单轮诊断报告', 'sm primary');
      const previewBtn = btn('预览报告', 'sm');
      previewBtn.hidden = true;
      let lastTurnReport = '';
      copyBtn.addEventListener('click', async () => {
        const rep = await reqJson(req, `/api/brain/recall-report?request_id=${encodeURIComponent(tid)}`);
        if (rep.ok && rep.data?.markdown) {
          lastTurnReport = rep.data.markdown;
          previewBtn.hidden = false;
          const ok = await copyText(rep.data.markdown);
          copyBtn.textContent = ok ? '已复制诊断报告 ✓' : '已生成（点击预览）';
          setTimeout(() => { copyBtn.textContent = '复制单轮诊断报告'; }, 2000);
        }
      });
      previewBtn.addEventListener('click', () => {
        if (!lastTurnReport) return;
        openBrainModal('单轮召回与注入诊断报告', `${formatTime(item.time)} · ${item.model}`, body => {
          body.append(el('pre', 'report-preview-box', lastTurnReport));
        });
      });
      headActions.append(copyBtn, previewBtn);
      head.append(headActions);

      const meta = el('p', 'muted', `${formatTime(item.time)} · ${item.model} · ${item.purpose_label || item.purpose}`);

      // Flat metric strip (Token, Cache, Cost) — never coerce missing cache to 0
      const metricStrip = el('div', 'flat-metric-strip');
      const cacheText = tk.cache_hit != null
        ? `${tk.cache_hit}${tk.cache_hit_rate != null ? ` (${tk.cache_hit_rate}%)` : ''}`
        : '未记录';
      const tCards = [
        ['快照预估 Token', tk.estimated != null ? String(tk.estimated) : '未记录'],
        ['实际输入 / 输出', tk.actual_input != null ? `${tk.actual_input} / ${tk.actual_output ?? '-'}` : '未计量（上游未返回）'],
        ['缓存命中', cacheText],
        ['记忆注入 Token', tk.memory_tokens != null ? String(tk.memory_tokens) : '未记录'],
        ['结算费用', item.cost_cny != null ? `¥${item.cost_cny}` : '暂无结算'],
      ];
      for (const [lbl, val] of tCards) {
        const c = el('div', 'flat-metric-cell');
        c.append(el('small', '', lbl), el('strong', '', val));
        metricStrip.append(c);
      }

      // 1. Six-layer Recall Chain (distinguish hit / not_hit / not_run / missing)
      const chainBlock = el('div', 'recall-section-block');
      chainBlock.append(el('h4', '', '实际召回链路'));
      const chainGrid = el('div', 'recall-chain-grid');
      const chainSteps = item.recall_chain || [];
      if (item.recall_evidence_state === 'missing') {
        chainGrid.append(el('p', 'muted', '该轮未记录召回追踪证据（证据缺失，不等同于未命中）。'));
      } else if (chainSteps.length) {
        for (const st of chainSteps) {
          const cell = el('div', 'recall-chain-step');
          let badgeText = '未命中';
          let badgeTone = '';
          if (st.state === 'missing') { badgeText = '证据缺失'; badgeTone = 'warn'; }
          else if (st.state === 'not_run') { badgeText = '未执行'; }
          else if (st.state === 'hit' || (st.count != null && st.count > 0)) { badgeText = `命中 ${st.count}`; badgeTone = 'good'; }
          cell.append(el('span', '', st.label), pill(badgeText, badgeTone));
          const base = st.key === 'relationship_base' ? st.detail : null;
          if (base && typeof base === 'object') {
            const size = value => Array.isArray(value) ? value.length : '?';
            cell.append(el('small', 'muted',
              `存储 ${base.stored_count ?? '?'} / 有效 ${base.active_count ?? '?'} / 初选 ${size(base.preselected_items)} / 选中 ${size(base.selected_items)} / 注入 ${st.count ?? '?'}`));
            if (base.reason) cell.append(el('small', 'muted', base.reason));
            if (base.dropped?.length) cell.append(el('small', 'muted',
              base.dropped.map(row => `${row.id}（${row.reason}）`).join('；')));
          }
          const leisureAssoc = st.key === 'natural_association'
            ? (item.leisure_association || item.recall?.leisure_association || null)
            : null;
          if (leisureAssoc && typeof leisureAssoc === 'object' && Object.keys(leisureAssoc).length) {
            const gateMode = leisureAssoc.wake_gate?.mode || leisureAssoc.context_mode || 'unknown';
            const simPart = typeof leisureAssoc.similarity === 'number'
              ? ` · 相似度 ${leisureAssoc.similarity.toFixed(2)}` : '';
            cell.append(el('small', 'muted',
              `自主门控：${gateMode} · ${leisureAssoc.reason || '未记录'}${simPart}`));
          }
          chainGrid.append(cell);
        }
      } else {
        chainGrid.append(el('p', 'muted', '暂无召回链路分层数据'));
      }
      chainBlock.append(chainGrid);

      // 2. Candidate vs Final Injected Comparison
      const diffBlock = el('div', 'recall-section-block');
      const cands = item.candidates || [];
      const injected = item.injected || [];
      const dropped = item.dropped_candidates || [];
      diffBlock.append(el('h4', '', `候选检索 (${cands.length}) 与最终注入 (${injected.length}) 对比`));
      const diffGrid = el('div', 'candidate-diff-grid');

      const injCol = el('div', 'candidate-col');
      injCol.append(el('small', 'muted', `最终注入记忆（${injected.length} 条）`));
      if (injected.length) {
        for (const m of injected) {
          const row = el('div', 'candidate-row');
          const mid = m.memory_id || m.id || '';
          const scoreStr = m.score != null ? ` · 相关度 ${Number(m.score).toFixed(2)}` : '';
          const layerStr = m.layer ? ` · ${m.layer}` : '';
          row.append(
            el('strong', '', m.title || mid || '记忆'),
            el('small', 'muted', `已注入${layerStr}${scoreStr}`),
          );
          injCol.append(row);
        }
      } else {
        injCol.append(el('p', 'muted', item.recall_evidence_state === 'missing' ? '召回证据缺失' : '本轮无注入记忆'));
      }

      const dropCol = el('div', 'candidate-col');
      dropCol.append(el('small', 'muted', `未入选候选（${dropped.length} 条）`));
      if (dropped.length) {
        for (const m of dropped) {
          const row = el('div', 'candidate-row');
          const mid = m.memory_id || m.id || '';
          const scoreStr = m.score != null ? ` · 相关度 ${Number(m.score).toFixed(2)}` : '';
          row.append(
            el('strong', '', m.title || mid || '候选记忆'),
            el('small', 'muted', `${m.drop_reason || '未记录未入选原因（证据缺失）'}${scoreStr}`),
          );
          dropCol.append(row);
        }
      } else {
        dropCol.append(el('p', 'muted', item.recall_evidence_state === 'missing' ? '召回证据缺失' : '无未入选候选'));
      }
      diffGrid.append(injCol, dropCol);
      diffBlock.append(diffGrid);

      // 3. Prompt Section Token Breakdown (explicitly labeled as pre-send snapshot estimate)
      const secBlock = el('div', 'recall-section-block');
      const sections = item.prompt_sections || [];
      const estSum = sections.reduce((acc, s) => acc + Number(s.tokens || 0), 0);
      const secHead = el('div', 'veil-card-head');
      secHead.append(
        el('h4', '', 'Prompt 分区 Token 分布（发送前快照估算）'),
        el('small', 'muted', tk.actual_input != null ? `分区估算合计 ${estSum} tok · 上游实测输入 ${tk.actual_input} tok` : `分区估算合计 ${estSum} tok`),
      );
      secBlock.append(secHead, el('small', 'muted', '各分区为发送前按规则估算，实际计费输入以上游模型返回的「实际输入 Token」为准。'));
      if (sections.length) {
        const maxSec = Math.max(1, ...sections.map(s => Number(s.tokens || 0)));
        const secList = el('div', 'prompt-sec-list');
        for (const sec of sections) {
          const tok = Number(sec.tokens || 0);
          const row = el('div', 'prompt-sec-row');
          const track = el('div', 'prompt-sec-track');
          const fill = el('div', 'prompt-sec-fill');
          fill.style.width = `${Math.max(3, Math.round((tok / maxSec) * 100))}%`;
          track.append(fill);
          row.append(
            el('span', '', sec.label || sec.name || '分区'),
            track,
            el('span', 'prompt-sec-val', `${tok} tok`),
          );
          secList.append(row);
        }
        secBlock.append(secList);
      } else {
        secBlock.append(el('p', 'muted', '该轮未记录 Prompt 分区明细'));
      }

      const det = el('details', 'tech-evidence');
      det.append(el('summary', '', '查看原始请求快照与追踪数据'));
      det.append(el('pre', 'diff-box', JSON.stringify(item, null, 2)));

      if (seq !== detailSeq) return;
      detailPane.replaceChildren(head, meta, metricStrip, chainBlock, diffBlock, secBlock, det);
    }

    split.append(listPane, detailPane);
    host.replaceChildren(filterBar, trendCard, split, pager);
    await loadTurns();
    return;
  }

  function describeHealthPeriod(period) {
    // An unfinished period has no counts. Zeros and "no snapshots" would look like a checked empty range.
    const lines = [];
    const samples = [];
    if (!period || !period.requested) return { lines, samples };
    if (period.status === 'incomplete' || period.status === 'unavailable') {
      lines.push({ text: '指定期间检查未完成，计数未统计。' });
      lines.push({ muted: true, text: '历史快照：本轮未完成，不判断有没有快照。' });
      return { lines, samples };
    }
    const growth = period.growth || {};
    lines.push({
      text: `期间新增：记忆 ${growth.memories_created ?? 0} 条 · 事件 ${growth.events_created ?? 0} 个 · 阶段 ${growth.event_stages ?? 0} 个 · 版本 ${growth.versions ?? 0} 条 · 认知版本变更 ${growth.learned_context_changes ?? 0} 条`,
    });
    lines.push({
      text: `期间重复：${period.duplicates?.count ?? 0} 组 · 碎片：${period.fragments?.count ?? 0} 条 · 悬空引用：${period.dangling?.count ?? 0} 处 · 索引异常：${period.index_anomalies?.count ?? 0} 条`,
    });
    const coverage = period.coverage || {};
    if (coverage.note) lines.push({ muted: true, text: `证据边界：${coverage.note}` });
    const hist = period.historical_state || {};
    if (hist.available && hist.delta_known === false) {
      lines.push({ text: `历史快照：${hist.observations || 0} 次完整记录，变化未知。` });
      if (hist.limit) lines.push({ muted: true, text: hist.limit });
    } else if (hist.available) {
      const delta = hist.delta || {};
      lines.push({
        text: `历史快照：${hist.observations || 0} 次 · 活跃记忆变化 ${delta.active_memories ?? '—'} · 事件变化 ${delta.events_total ?? '—'}`,
      });
      lines.push({ muted: true, text: hist.limit || '快照只记录每日总量。' });
      if (hist.coverage === 'partial') lines.push({ muted: true, text: `快照覆盖不完整：${hist.reason || ''}` });
    } else {
      lines.push({ muted: true, text: '该时间段没有巡检历史快照；只报告有库内时间字段支持的期间变化，不推断当时整体健康状态。' });
    }
    for (const [key, label] of [['duplicates', '重复'], ['fragments', '碎片'], ['dangling', '悬空引用'], ['index_anomalies', '索引异常']]) {
      for (const ev of (period[key]?.evidences || []).slice(0, 4)) samples.push(`${label}｜${ev.label || ''}：${ev.note || ''}`);
    }
    return { lines, samples };
  }

  // =========================================================================
  // 6. Strictly Read-Only P5 Health Center (brain-health)
  // =========================================================================
  function validHealthSettings(settings) {
    return Boolean(settings && typeof settings === 'object'
      && typeof settings.enabled === 'boolean'
      && ['daily_night', 'every_6h', 'manual_only'].includes(settings.schedule));
  }

  async function renderHealthWorkspace(host, req) {
    host.replaceChildren(el('p', '', '正在加载只读健康报告与巡检设置…'));
    let healthSettings = null;
    let healthControls = null;
    let healthSaveSeq = 0;
    let filterFrom = '';
    let filterTo = '';

    async function drawHealth(payload) {
      const report = payload?.report || payload || {};
      // A scan response carries only the report; keep the saved settings instead of redrawing
      // the form with defaults that a later save would write back to disk. A malformed/missing
      // settings payload must not produce a saveable form: that form's defaults would overwrite
      // the real schedule on the next save.
      const candidate = payload && Object.prototype.hasOwnProperty.call(payload, 'settings')
        ? payload.settings : healthSettings;
      if (!validHealthSettings(candidate)) {
        host.replaceChildren(renderEmptyState(
          '巡检设置未能加载，已停止显示保存表单',
          '为避免用默认值覆盖你的原配置，请检查后端服务后重新打开本页。'));
        return;
      }
      const settings = candidate;
      healthSettings = settings;
      const sum = report.summary || { status: 'good', status_label: '尚未运行检查', total_checks: 0, good_count: 0, warn_count: 0, error_count: 0 };
      const checks = report.checks || [];
      healthWarnFlag = Boolean(sum.warn_count || sum.error_count);
      const dot = document.getElementById('subnav-health-dot');
      if (dot) dot.hidden = !healthWarnFlag;

      const wrap = el('div', 'workspace-content');

      // Summary banner
      const banner = el('div', 'health-summary-banner');
      const lead = el('div', 'health-status-lead');
      const iconCls = sum.warn_count || sum.error_count ? 'warn' : 'good';
      const statusBadge = el('div', `health-status-icon ${iconCls}`);
      statusBadge.append(el('span', `health-badge-dot ${iconCls}`));
      lead.append(statusBadge);
      const leadText = el('div');
      const scopeLabel = report.scope?.label || (report.from_date || report.to_date ? '全库当前 + 指定期间' : '全库当前');
      leadText.append(
        el('h3', '', sum.status_label || '状态正常'),
        el('small', '', `检查时间: ${formatTime(report.scanned_at)} · 耗时 ${report.duration_ms ?? 0}ms · 数据范围: ${scopeLabel} · 只读核验 · 零模型消耗`),
      );
      lead.append(leadText);

      const scanBtn = btn('立即全面检查', 'primary sm');
      const copyReportBtn = btn('复制巡检报告', 'sm');
      const previewHealthBtn = btn('预览报告', 'sm');
      previewHealthBtn.hidden = true;
      const exportBtn = btn('导出脱敏报告', 'sm');
      const bannerMsg = el('small', '', '');
      const btnGroup = el('div', 'workspace-links');
      btnGroup.append(scanBtn, copyReportBtn, previewHealthBtn, exportBtn, bannerMsg);
      banner.append(lead, btnGroup);

      // Date range filter bar for Health Scan
      const rangeBar = el('div', 'inline-controls');
      const fromInp = el('input', 'veil-input');
      fromInp.type = 'date';
      fromInp.value = filterFrom;
      fromInp.ariaLabel = '巡检开始日期';
      const toInp = el('input', 'veil-input');
      toInp.type = 'date';
      toInp.value = filterTo;
      toInp.ariaLabel = '巡检结束日期';
      const rangeScanBtn = btn('按日期范围巡检', 'sm');
      rangeBar.append(el('small', 'muted', '指定日期范围：'), fromInp, el('span', '', '至'), toInp, rangeScanBtn);

      // Period slice: shown separately from whole-database health so a filtered run is never
      // mistaken for "this was the database's state back then". Missing history is stated, not guessed.
      const period = report.period || {};
      let periodCard = null;
      if (period.requested) {
        periodCard = el('article', 'veil-card');
        const pHead = el('div', 'veil-card-head');
        pHead.append(el('h3', '', '指定期间变化与异常（与全库当前分开）'),
          pill(`${report.from_date || '最早'} 至 ${report.to_date || '最新'}`, 'warn'));
        const described = describeHealthPeriod(period);
        const pBody = el('div', 'health-kv');
        for (const line of described.lines) pBody.append(el('div', line.muted ? 'muted' : '', line.text));
        if (described.samples.length) {
          const ul = el('ul', 'model-list');
          for (const line of described.samples) ul.append(el('li', '', line));
          pBody.append(ul);
        }
        periodCard.append(pHead, pBody);
      }

      let lastHealthReportText = '';

      // KPI stats row inside banner
      const stats = report.stats || {};
      const kpis = el('div', 'health-kpis');
      for (const [lbl, val] of [
        ['活跃记忆', stats.active_memories ?? '—'],
        ['事件故事', stats.events_total ?? '—'],
        ['向量索引', stats.vectors_total ?? '—'],
        ['通过项', sum.good_count ?? 0],
        ['需关注', (sum.warn_count ?? 0) + (sum.error_count ?? 0)],
      ]) {
        const box = el('div', 'health-kpi-item');
        box.append(el('small', '', lbl), el('strong', '', val));
        kpis.append(box);
      }

      // Auto-schedule setting bar
      const schedBar = el('div', 'veil-card');
      const schedHead = el('div', 'veil-card-head');
      schedHead.append(el('h3', '', '定期只读巡检设置'), pill('不修改任何记忆'));
      const controls = el('div', 'filter-bar');
      const enabledChk = document.createElement('input');
      enabledChk.type = 'checkbox';
      enabledChk.checked = Boolean(settings.enabled);
      const chkLabel = el('label', '', '启用定期巡检 ');
      chkLabel.prepend(enabledChk);
      const schedSel = document.createElement('select');
      for (const [val, txt] of [['daily_night', '每日夜间自动检查'], ['every_6h', '每 6 小时检查'], ['manual_only', '仅手动检查']]) {
        const o = el('option', '', txt);
        o.value = val;
        schedSel.append(o);
      }
      schedSel.value = settings.schedule || 'daily_night';
      healthControls = { enabledChk, schedSel };
      const saveSchedBtn = btn('保存巡检设置', 'sm');
      const schedMsg = el('small', '', '');
      saveSchedBtn.addEventListener('click', async () => {
        const saveSeq = ++healthSaveSeq;
        saveSchedBtn.disabled = true;
        try {
          const res = await reqJson(req, '/api/brain/health/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // idle_only is not editable here; omitting it keeps whatever the backend has saved.
            body: JSON.stringify({ enabled: enabledChk.checked, schedule: schedSel.value }),
          });
          if (saveSeq !== healthSaveSeq) return;  // a newer save owns the form
          if (res.ok && res.data?.settings) {
            healthSettings = res.data.settings;
            // A concurrent manual scan may have rebuilt the form from the old cache; update the
            // live controls too, otherwise the next save would write the old cycle back.
            if (healthControls) {
              healthControls.enabledChk.checked = Boolean(healthSettings.enabled);
              healthControls.schedSel.value = healthSettings.schedule || 'daily_night';
            }
            schedMsg.textContent = '已保存设置';
          } else {
            schedMsg.textContent = res.data?.error || '保存失败';
          }
        } catch (err) {
          schedMsg.textContent = err?.message || '保存失败';
        } finally {
          if (saveSeq === healthSaveSeq) saveSchedBtn.disabled = false;
        }
      });
      controls.append(chkLabel, schedSel, saveSchedBtn, schedMsg);
      schedBar.append(schedHead, controls);

      // Checks list (human summary upfront, technical evidence collapsed)
      const sorted = [...checks].sort((a, b) => {
        const score = c => (c.severity === 'error' ? 0 : c.severity === 'warn' ? 1 : 2);
        return score(a) - score(b);
      });
      const checksWrap = el('div', '');
      for (const chk of sorted) {
        const item = el('article', 'health-item');
        const cHead = el('div', 'veil-card-head');
        cHead.append(
          el('h3', '', chk.title || ''),
          pill(chk.status_label || (chk.severity === 'good' ? '正常' : '需关注'), chk.severity === 'good' ? 'good' : (chk.severity === 'error' ? 'danger' : 'warn')),
        );
        item.append(cHead, el('p', '', chk.impact || ''));

        const tech = el('details', 'tech-evidence');
        if (chk.severity === 'warn' || chk.severity === 'error') tech.open = true;
        tech.append(el('summary', '', '查看检查依据与样本证据'));
        const kv = el('div', 'health-kv');
        kv.append(
          el('div', '', `检查对象：${chk.object || '记忆数据库'}`),
          el('div', '', `判断依据：${chk.basis || '—'}`),
          el('div', '', `数据来源：${chk.source || '主记忆库'}`),
          el('div', '', `可信度：${chk.confidence || '高可信'}`),
        );
        const scopeNames = { current: '全库当前', 'current+range': '全库当前 + 指定期间', range: '指定期间', 'range (all logs)': '日志当前窗口' };
        kv.append(el('div', '', '数据范围：' + (scopeNames[chk.scope] || '全库当前')));
        tech.append(kv);
        if (Array.isArray(chk.evidences) && chk.evidences.length) {
          const evList = el('ul', 'model-list');
          for (const ev of chk.evidences) {
            const li = el('li', '', `${ev.label || ''}: ${ev.note || ''}${ev.basis ? `（依据：${ev.basis}）` : ''}`);
            if (ev.memId) {
              const jump = btn('查看记忆', 'sm ghost');
              jump.addEventListener('click', () => window.veilWorkspace?.openMemory(ev.memId));
              li.append(' ', jump);
            } else if (ev.eventId) {
              const jump = btn('查看事件', 'sm ghost');
              jump.addEventListener('click', () => window.veilWorkspace?.openEvent(ev.eventId));
              li.append(' ', jump);
            }
            evList.append(li);
          }
          tech.append(evList);
        }
        item.append(tech);
        checksWrap.append(item);
      }

      const runScanWithRange = async (btnEl, defaultText) => {
        filterFrom = fromInp.value;
        filterTo = toInp.value;
        btnEl.disabled = true;
        btnEl.textContent = '巡检中…';
        try {
          const res = await reqJson(req, '/api/brain/health/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ from: filterFrom, to: filterTo }),
          });
          if (!res.ok || !res.data?.report) {
            bannerMsg.textContent = res.data?.error || '巡检失败，请稍后重试。';
            return;
          }
          await drawHealth({ report: res.data.report, settings: healthSettings });
        } catch (err) {
          bannerMsg.textContent = err?.message || '巡检失败，请稍后重试。';
        } finally {
          btnEl.disabled = false;
          btnEl.textContent = defaultText;
        }
      };

      scanBtn.addEventListener('click', () => runScanWithRange(scanBtn, '立即全面检查'));
      rangeScanBtn.addEventListener('click', () => runScanWithRange(rangeScanBtn, '按日期范围巡检'));

      copyReportBtn.addEventListener('click', async () => {
        copyReportBtn.disabled = true;
        try {
          const res = await req('/api/brain/health/export?format=markdown');
          const payload = await res.json().catch(() => null);
          if (!res.ok || !payload || typeof payload.text !== 'string') {
            bannerMsg.textContent = '复制失败：后端没有返回报告正文。';
            return;
          }
          lastHealthReportText = payload.text;
          previewHealthBtn.hidden = false;
          const ok = await copyText(payload.text);
          bannerMsg.textContent = ok ? '已复制脱敏巡检报告 ✓' : '已生成报告（点击预览报告查看）';
        } catch (err) {
          bannerMsg.textContent = err?.message || '复制失败。';
        } finally {
          copyReportBtn.disabled = false;
        }
      });

      exportBtn.addEventListener('click', async () => {
        exportBtn.disabled = true;
        try {
          const res = await req('/api/brain/health/export?format=markdown');
          // The endpoint replies with {format, text}; res.text() would save the JSON wrapper.
          const payload = await res.json().catch(() => null);
          if (!res.ok || !payload || typeof payload.text !== 'string') {
            bannerMsg.textContent = '导出失败：后端没有返回报告正文。';
            return;
          }
          const blob = new Blob([payload.text], { type: 'text/markdown;charset=utf-8' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `veil-brain-health-${new Date().toISOString().slice(0, 10)}.md`;
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          bannerMsg.textContent = '已导出脱敏报告。';
        } catch (err) {
          bannerMsg.textContent = err?.message || '导出失败。';
        } finally {
          exportBtn.disabled = false;
        }
      });

      previewHealthBtn.addEventListener('click', () => {
        if (!lastHealthReportText) return;
        openBrainModal('健康巡检脱敏报告', '严格只读检查摘要', body => {
          body.append(el('pre', 'report-preview-box', lastHealthReportText));
        });
      });

      wrap.append(banner, rangeBar, ...(periodCard ? [periodCard] : []), kpis, schedBar, checksWrap);
      host.replaceChildren(wrap);
    }

    const healthRes = await reqJson(req, '/api/brain/health');
    if (!healthRes.ok || !healthRes.data || !validHealthSettings(healthRes.data.settings)) {
      // Do not render a writable settings form from defaults after a failed/partial read: a later
      // save would silently overwrite the real schedule. Show the failure and let the user retry.
      host.replaceChildren(renderEmptyState('暂时无法读取健康巡检数据', '请检查后端服务后重新打开本页。'));
      return;
    }
    await drawHealth(healthRes.data);
  }

  window.veilBrain = {
    isBrainPage: page => BRAIN_PAGES.has(page),
    hasHealthWarnings: () => healthWarnFlag,
    mountMemoryLibraryAddon,
    openMemory(memId) {
      mountMemoryLibraryAddon(lastReq);
      const addon = document.getElementById('brain-library-addon');
      addon?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      addon?._openMemoryById?.(memId);
    },
    openEvent(eventId) {
      const host = document.querySelector('#workspace-events-view .workspace-content');
      if (host) renderEventsWorkspace(host, lastReq, eventId);
    },
    openRecallTurn(turnId) {
      const host = document.querySelector('#brain-recall-view .workspace-content');
      if (host) renderRecallWorkspace(host, lastReq, turnId);
    },
    async renderPage(page, host, req) {
      lastReq = req || lastReq;
      if (page === 'brain-home') return renderBrainHome(host, lastReq);
      if (page === 'workspace-events') return renderEventsWorkspace(host, lastReq);
      if (page === 'brain-cognition') return renderCognitionWorkspace(host, lastReq);
      if (page === 'brain-recall') return renderRecallWorkspace(host, lastReq);
      if (page === 'brain-health') return renderHealthWorkspace(host, lastReq);
    },
  };
})();
