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

  function formatTime(iso) {
    if (!iso) return '—';
    return String(iso).replace('T', ' ').slice(0, 19);
  }

  function shortSha(sha) {
    return sha ? String(sha).slice(0, 8) : '—';
  }

  async function reqJson(req, url, options) {
    const res = await req(url, options);
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    return { ok: res.ok, status: res.status, data };
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

  function renderEmptyState(title, subtitle) {
    const box = el('div', 'empty-pane');
    box.append(el('span', 'empty-pane-mark'), el('strong', '', title), el('small', '', subtitle));
    return box;
  }

  // =========================================================================
  // 1. P4 Memory Overview (brain-home)
  // =========================================================================
  async function renderBrainHome(host, req) {
    host.replaceChildren(el('p', '', '正在汇总 VEIL Brain 概览数据…'));
    const { ok, data } = await reqJson(req, '/api/brain/overview');
    if (!ok || !data || !data.counts) {
      host.replaceChildren(renderEmptyState('暂时无法读取记忆概览', '请确认当前角色目录与后端服务正常。'));
      return;
    }

    const c = data.counts;
    const hSum = data.health_summary;
    healthWarnFlag = Boolean(hSum && (hSum.warn_count || hSum.error_count));

    const kpiGrid = el('div', 'kpi-grid');
    const kpiDefs = [
      { label: '活跃记忆', val: c.active ?? 0, sub: `置顶 ${c.pinned ?? 0} · 归档 ${c.archived ?? 0}`, page: 'memory' },
      { label: '共同经历', val: c.events_total ?? 0, sub: `待重整 ${c.events_pending_story ?? 0} · 待复核 ${c.events_pending_review ?? 0}`, page: 'workspace-events' },
      { label: '本周动态', val: `+${c.week_added ?? 0} / 改${c.week_updated ?? 0}`, sub: `待补向量 ${c.pending_vector ?? 0} · 墓碑 ${c.tombstones ?? 0}`, page: 'brain-recall' },
      {
        label: '健康巡检',
        val: hSum ? (hSum.error_count ? `异常 ${hSum.error_count}` : (hSum.warn_count ? `关注 ${hSum.warn_count}` : '良好')) : '待首次检查',
        sub: data.health_scanned_at ? `最近 ${formatTime(data.health_scanned_at)}` : '点击进入只读巡检',
        page: 'brain-health',
      },
    ];
    for (const k of kpiDefs) {
      const card = el('article', 'veil-card kpi-card');
      const top = el('div', 'kpi-top');
      top.append(el('span', '', k.label), el('span', 'kpi-arrow', '→'));
      card.append(top, el('div', 'kpi-val', k.val), el('div', 'kpi-sub', k.sub));
      card.addEventListener('click', () => window.veilWorkspace?.go(k.page));
      kpiGrid.append(card);
    }

    const overviewGrid = el('div', 'overview-grid');

    // Left: Dual-stack trend chart + 6 layers
    const leftCol = el('div', 'workspace-content');
    const chartCard = el('article', 'veil-card');
    const chartHead = el('div', 'veil-card-head');
    chartHead.append(el('h3', '', '记忆增长与修订趋势'));
    const seg = el('div', 'seg-tabs');
    const btn7d = el('button', '', '近 7 天');
    const btn30d = el('button', '', '近 4 周');
    btn7d.type = btn30d.type = 'button';
    seg.append(btn7d, btn30d);
    chartHead.append(seg);

    const legend = el('div', 'chart-legend');
    const l1 = el('span');
    l1.append(el('i', 'legend-dot new'), '新增记忆');
    const l2 = el('span');
    l2.append(el('i', 'legend-dot revised'), '修订更新');
    legend.append(l1, l2);

    const barsWrap = el('div', 'growth-bars-wrap');
    const axisX = el('div', 'growth-axis-x');
    const drawTrend = mode => {
      btn7d.setAttribute('aria-pressed', String(mode === '7d'));
      btn30d.setAttribute('aria-pressed', String(mode === '30d'));
      const series = (data.trends && data.trends[mode]) || [];
      const maxVal = Math.max(1, ...series.map(pt => (pt.added || 0) + (pt.updated || 0)));
      barsWrap.replaceChildren();
      axisX.replaceChildren();
      for (const pt of series) {
        const col = el('div', 'growth-col');
        const stack = el('div', 'bar-stack');
        const hAdded = Math.round(((pt.added || 0) / maxVal) * 116);
        const hUpd = Math.round(((pt.updated || 0) / maxVal) * 116);
        const segNew = el('div', 'bar-seg-new');
        segNew.style.height = `${hAdded}px`;
        segNew.title = `${pt.label} 新增 ${pt.added || 0}`;
        const segRev = el('div', 'bar-seg-revised');
        segRev.style.height = `${hUpd}px`;
        segRev.title = `${pt.label} 修订 ${pt.updated || 0}`;
        stack.append(segNew, segRev);
        col.append(stack);
        barsWrap.append(col);
        axisX.append(el('span', '', pt.label));
      }
    };
    btn7d.addEventListener('click', () => drawTrend('7d'));
    btn30d.addEventListener('click', () => drawTrend('30d'));
    drawTrend('7d');
    chartCard.append(chartHead, legend, barsWrap, axisX);

    // 6-layer statuses
    const layerCard = el('article', 'veil-card');
    const layerHead = el('div', 'veil-card-head');
    layerHead.append(el('h3', '', '六层记忆系统运行态'), pill('实时状态'));
    const layerGrid = el('div', 'layer-list');
    for (const layer of data.layers || []) {
      const item = el('div', 'layer-item');
      const top = el('div', 'layer-item-top');
      top.append(el('strong', '', layer.name), pill(layer.status_text || '未知', layer.tone || ''));
      item.append(top, el('small', '', layer.detail || ''));
      layerGrid.append(item);
    }
    layerCard.append(layerHead, layerGrid);
    leftCol.append(chartCard, layerCard);

    // Right: Recent Brain timeline
    const rightCol = el('article', 'veil-card');
    const tlHead = el('div', 'veil-card-head');
    tlHead.append(el('h3', '', '最近记忆与事件轨迹'));
    const tlBtn = btn('打开记忆库', 'sm');
    tlBtn.addEventListener('click', () => window.veilWorkspace?.go('memory'));
    tlHead.append(tlBtn);
    rightCol.append(tlHead);
    const tl = el('ul', 'veil-timeline');
    const acts = data.activities || [];
    if (!acts.length) {
      rightCol.append(renderEmptyState('近期暂无写入动态', '新的对话整理和事件记录会按时间出现在这里。'));
    } else {
      for (const act of acts) {
        const li = el('li', 'tl-item');
        li.append(el('span', act.kind === 'event' ? 'tl-dot gold' : 'tl-dot'));
        const card = el('button', 'tl-card');
        card.type = 'button';
        card.append(el('div', 'tl-title', act.title || act.kind), el('div', 'tl-meta', act.meta || formatTime(act.time)));
        if (act.detail) card.append(el('div', 'tl-detail', act.detail));
        card.addEventListener('click', () => {
          if (act.event_id) window.veilWorkspace?.openEvent(act.event_id);
          else if (act.memory_id) window.veilWorkspace?.openMemory(act.memory_id);
        });
        li.append(card);
        tl.append(li);
      }
      rightCol.append(tl);
    }

    overviewGrid.append(leftCol, rightCol);
    host.replaceChildren(kpiGrid, overviewGrid);
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

    const addon = el('section', 'veil-card');
    addon.id = 'brain-library-addon';
    addon.style.marginTop = '14px';

    const head = el('div', 'veil-card-head');
    const titleWrap = el('div');
    titleWrap.append(el('h3', '', 'P4 全量记忆浏览器（分层检索 · 版本 Diff · 并发锁定）'));
    const statsLabel = el('small', '', '支持置顶/关联/碎片过滤，保存时校验 SHA-256 乐观锁');
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
    const kindSel = mkSelect([['all', '类型: 全部'], ['pinned', '仅置顶'], ['event_link', '关联事件'], ['fragment', '短碎片']]);
    const sortSel = mkSelect([['updated', '排序: 最近更新'], ['recalled', '排序: 召回频次'], ['importance', '排序: 重要度'], ['created', '排序: 创建时间']]);
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

    let state = { offset: 0, limit: 30, selectedId: null, items: [], filtered: 0, loadSeq: 0 };
    const drafts = new Map();

    function openDetail(item) {
      state.selectedId = item.memory_id;
      for (const node of listPane.querySelectorAll('.index-item')) {
        node.setAttribute('aria-current', String(node.dataset.memId === item.memory_id));
      }
      readerPane.replaceChildren();

      const badgeRow = el('div', 'index-tags');
      badgeRow.append(pill(item.kind || 'memory', 'rose'), pill(item.bucket || 'dynamic'));
      if (item.pinned) badgeRow.append(pill('置顶', 'gold'));
      if (item.locked) badgeRow.append(pill('锁定', 'warn'));
      if (item.archived) badgeRow.append(pill('已归档'));
      if (item.vector_state && item.vector_state !== 'ready') badgeRow.append(pill(`向量:${item.vector_state}`, 'warn'));

      const title = el('h3', 'reader-title', item.title);
      const meta = el('div', 'reader-meta-grid');
      meta.append(
        el('span', '', `ID: ${item.memory_id}`),
        el('span', '', `更新: ${formatTime(item.updated_at)}`),
        el('span', '', `召回: ${item.recalled ?? 0} 次`),
        el('span', '', `版本: ${item.version_count ?? 0}`),
        el('span', '', `校验: ${shortSha(item.sha256)}`),
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
        const box = el('article', 'veil-card');
        box.style.marginTop = '8px';
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
      const saveBtn = btn('保存修改（乐观锁校验）', 'sm primary');
      const verBtn = btn('查看版本与 Diff', 'sm');
      const archBtn = btn(item.archived ? '恢复到活跃记忆' : '移入归档', 'sm');
      const delBtn = btn('申请删除', 'sm danger');

      const extraArea = el('div', '');

      saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true;
        actionStatus.textContent = '正在校验 SHA-256 并保存…';
        try {
          const res = await reqJson(lastReq, '/api/memory/edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id: item.memory_id,
              // panel_edit only reads body['changes']; the top-level content used to be dropped.
              changes: { content: bodyEditor.value },
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
          drafts.delete(item.memory_id);
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
            const card = el('div', 'veil-card');
            card.style.marginTop = '8px';
            card.append(el('strong', '', `${formatTime(v.time)} · ${v.operation_reason || '编辑'}`));
            if (v.diff) card.append(buildLineDiffNode(v.diff));
            else if (v.old_content) card.append(el('div', 'diff-box', v.old_content.slice(0, 400)));
            extraArea.append(card);
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
          drafts.delete(item.memory_id);
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
          state.selectedId = null;
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

      readerPane.append(bodyEditor, draftArea, actions, actionStatus, extraArea);
    }

    async function loadLibrary(focusId) {
      if (focusId) state.selectedId = focusId;
      // Snapshot the query and a sequence number: a slower response for an older query must not
      // overwrite a newer filter's list, page number or error state.
      const seq = ++state.loadSeq;
      const qs = new URLSearchParams({
        q: qInput.value.trim(),
        status: statusSel.value,
        kind: kindSel.value,
        sort: sortSel.value,
        offset: String(state.offset),
        limit: String(state.limit),
      });
      const { ok, data } = await reqJson(lastReq, `/api/memory/library?${qs}`);
      if (seq !== state.loadSeq) return;
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
      for (const item of state.items) {
        const card = el('button', 'index-item');
        card.type = 'button';
        card.dataset.memId = item.memory_id;
        card.setAttribute('aria-current', String(item.memory_id === state.selectedId));
        const top = el('div', 'index-item-top');
        top.append(el('strong', '', item.title), el('small', '', formatTime(item.updated_at).slice(0, 10)));
        const tags = el('div', 'index-tags');
        tags.append(pill(item.kind || 'memory'));
        if (item.pinned) tags.append(pill('置顶', 'gold'));
        if (item.recalled) tags.append(pill(`召回 ${item.recalled}`));
        card.append(top, el('div', 'index-preview', item.body || item.why || ''), tags);
        card.addEventListener('click', () => openDetail(item));
        listPane.append(card);
        if (state.selectedId === item.memory_id) openDetail(item);
      }
    }

    async function openMemoryById(memId) {
      if (!memId) return;
      const seq = ++state.loadSeq;
      const qs = new URLSearchParams({ q: String(memId), status: 'all', kind: 'all',
                                       sort: 'updated', offset: '0', limit: '30' });
      const { ok, data } = await reqJson(lastReq, `/api/memory/library?${qs}`);
      if (seq !== state.loadSeq || !ok || !data) return;
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
    host.replaceChildren(el('p', '', '正在加载共同经历与事件故事…'));
    const { ok, data } = await reqJson(req, '/api/memory/events');
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
    const versions = verRes.data?.items || verRes.data?.versions || [];

    const grid = el('div', 'overview-grid');
    const relCard = el('article', 'veil-card');
    relCard.append(el('h3', '', '关系阶段与核心羁绊'));
    const relText = page.relationship || page.relationship_base || page.summary || '（暂无关系描述）';
    relCard.append(el('div', 'reader-prose', typeof relText === 'string' ? relText : JSON.stringify(relText, null, 2)));
    const fullProfileBtn = btn('打开完整 Peter 档案页', 'sm');
    fullProfileBtn.addEventListener('click', () => window.veilWorkspace?.go('peter'));
    relCard.append(fullProfileBtn);

    const learnedCard = el('article', 'veil-card');
    learnedCard.append(el('h3', '', '习得偏好与约定记忆'));
    const learnedText = page.learned_context || page.learned || '';
    learnedCard.append(el('div', 'reader-prose', typeof learnedText === 'string' ? learnedText : JSON.stringify(learnedText, null, 2)));

    if (versions.length) {
      const verTitle = el('h4', '', `近期认知版本历史 (${versions.length})`);
      learnedCard.append(verTitle);
      for (const v of versions.slice(0, 6)) {
        const box = el('div', 'layer-item');
        box.style.marginBottom = '8px';
        box.append(el('strong', '', `${formatTime(v.time)} · ${v.reason || v.action || '自动更新'}`));
        if (v.diff) box.append(buildLineDiffNode(v.diff));
        learnedCard.append(box);
      }
    }

    grid.append(relCard, learnedCard);
    host.replaceChildren(grid);
  }

  // =========================================================================
  // 5. Recall & Injection Diagnostics (brain-recall)
  // =========================================================================
  async function renderRecallWorkspace(host, req, focusTurnId = null) {
    host.replaceChildren(el('p', '', '正在读取记忆召回链路与开销快照…'));
    const snapRes = await reqJson(req, '/api/request-snapshots?limit=24');
    const combined = snapRes.data?.snapshots || snapRes.data?.items || [];

    if (!combined.length) {
      host.replaceChildren(renderEmptyState('暂无召回追踪记录', '完成一轮聊天后，这里会展示每一层的 Token 预算、命中的记忆与成本。'));
      return;
    }

    const split = el('div', 'split-reader');
    const listPane = el('div', 'index-pane');
    const detailPane = el('article', 'veil-card reader-pane');
    let selectedId = focusTurnId || combined[0].request_id || combined[0].id || '';
    let detailSeq = 0;

    async function showTurn(item) {
      const tid = String(item.request_id || item.id || '');
      selectedId = tid;
      const seq = ++detailSeq;
      for (const n of listPane.querySelectorAll('.index-item')) {
        n.setAttribute('aria-current', String(n.dataset.turnId === tid));
      }
      detailPane.replaceChildren(el('p', '', '正在加载该轮召回层级详情…'));

      let traceData = item;
      if (tid) {
        const traceRes = await reqJson(req, `/api/diagnostics/trace?request_id=${encodeURIComponent(tid)}`);
        if (seq !== detailSeq) return;  // A -> B -> A: only the newest request owns the pane
        if (traceRes.ok && traceRes.data) traceData = { ...item, ...traceRes.data };
      }

      detailPane.replaceChildren();
      detailPane.append(el('h3', 'reader-title', `轮次 ${tid || '最近对话'}`));

      const recall = traceData.recall || traceData.chain?.recall || traceData;
      const costs = traceData.costs || traceData.chain?.costs;
      const tags = el('div', 'index-tags');
      if (recall.retrieval_mode) tags.append(pill(`模式: ${recall.retrieval_mode}`, 'rose'));
      if (traceData.model) tags.append(pill(traceData.model));
      if (Array.isArray(costs)) {
        const settled = costs.filter(row => row && row.type === 'settle');
        const settledTotal = settled.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
        if (settled.length) tags.append(pill(`费用 ¥${settledTotal.toFixed(2)}（已结算 ${settled.length} 笔）`, 'gold'));
        else if (costs.length) tags.append(pill(`费用结算中（${costs.length} 条）`, 'warn'));
      } else if (costs?.total_cost != null) {
        tags.append(pill(`费用 ¥${costs.total_cost}`, 'gold'));
      }
      detailPane.append(tags);

      // Layer breakdown
      const layers = [
        ['关系底座', recall.relationship_base],
        ['开场记忆', recall.opening],
        ['相关召回', recall.relevant_recall],
        ['自然联想', recall.natural_association],
        ['延续记忆', recall.carried],
        ['向量召回', recall.vector],
      ];
      const layerGrid = el('div', 'layer-list');
      layerGrid.style.margin = '12px 0';
      for (const [label, val] of layers) {
        if (!val && val !== 0) continue;
        const card = el('div', 'layer-item');
        const cnt = Array.isArray(val) ? val.length : (val?.count ?? val?.items?.length ?? '已注入');
        card.append(el('strong', '', label), el('small', '', typeof val === 'string' ? val.slice(0, 80) : `命中 ${cnt}`));
        layerGrid.append(card);
      }
      detailPane.append(layerGrid);

      const hits = recall.items || recall.memories || [];
      if (Array.isArray(hits) && hits.length) {
        detailPane.append(el('h4', '', `命中记忆 (${hits.length})`));
        const hitList = el('div', 'workspace-links');
        for (const h of hits) {
          const mid = h.memory_id || h.id;
          const b = btn(`${h.title || mid || '记忆'}${h.score != null ? ` (${Number(h.score).toFixed(2)})` : ''}`, 'sm');
          if (mid) b.addEventListener('click', () => window.veilWorkspace?.openMemory(mid));
          hitList.append(b);
        }
        detailPane.append(hitList);
      }

      if (tid) {
        const rawDetails = el('details', 'tech-evidence');
        const sum = el('summary', '', '展开该轮完整 Prompt 分区快照（只读）');
        const body = el('pre', 'diff-box', '点击展开后按需加载…');
        rawDetails.append(sum, body);
        rawDetails.addEventListener('toggle', async () => {
          if (!rawDetails.open || rawDetails.dataset.loaded === 'true' || rawDetails.dataset.loading === 'true') return;
          rawDetails.dataset.loading = 'true';
          body.textContent = '正在读取完整快照…';
          try {
            const snapDetail = await reqJson(req, `/api/request-snapshot?id=${encodeURIComponent(tid)}`);
            if (!snapDetail.ok || !snapDetail.data) {
              throw new Error(snapDetail.data?.error || '快照读取失败，收起后再展开可重试。');
            }
            body.textContent = JSON.stringify(snapDetail.data, null, 2);
            if (snapDetail.data.truncated) {
              rawDetails.append(el('p', 'muted', '接口返回的快照已标记为截断。'));
            }
            rawDetails.dataset.loaded = 'true';
          } catch (err) {
            body.textContent = err?.message || '快照读取失败，收起后再展开可重试。';
          } finally {
            delete rawDetails.dataset.loading;
          }
        });
        detailPane.append(rawDetails);
      }
    }

    for (const item of combined) {
      const tid = String(item.request_id || item.id || '');
      const card = el('button', 'index-item');
      card.type = 'button';
      card.dataset.turnId = tid;
      card.setAttribute('aria-current', String(tid === String(selectedId)));
      const top = el('div', 'index-item-top');
      top.append(el('strong', '', item.user_preview || item.title || `请求 ${tid.slice(0, 10)}`), el('small', '', formatTime(item.time || item.created_at).slice(11, 19)));
      card.append(top, el('div', 'index-preview', item.model || item.recall?.retrieval_mode || '召回诊断记录'));
      card.addEventListener('click', () => showTurn(item));
      listPane.append(card);
    }

    split.append(listPane, detailPane);
    host.replaceChildren(split);
    const firstMatch = combined.find(x => String(x.request_id || x.id) === String(selectedId)) || combined[0];
    if (firstMatch) await showTurn(firstMatch);
  }

  // =========================================================================
  // 6. Strictly Read-Only P5 Health Center (brain-health)
  // =========================================================================
  async function renderHealthWorkspace(host, req) {
    host.replaceChildren(el('p', '', '正在加载只读健康报告与巡检设置…'));
    let healthSettings = null;

    async function drawHealth(payload) {
      const report = payload?.report || payload || {};
      // A scan response carries only the report; keep the saved settings instead of redrawing
      // the form with defaults that a later save would write back to disk.
      const settings = payload?.settings || healthSettings || { enabled: true, schedule: 'daily_night', idle_only: true };
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
      leadText.append(
        el('h3', '', sum.status_label || '状态正常'),
        el('small', '', `检查时间: ${formatTime(report.scanned_at)} · 耗时 ${report.duration_ms ?? 0}ms · 严格只读 (?mode=ro) · 零付费 Token`),
      );
      lead.append(leadText);

      const scanBtn = btn('立即全面检查（只读）', 'primary');
      const exportBtn = btn('导出脱敏报告', '');
      const bannerMsg = el('small', '', '');
      const btnGroup = el('div', 'workspace-links');
      btnGroup.append(scanBtn, exportBtn, bannerMsg);
      banner.append(lead, btnGroup);

      // KPI stats row inside banner
      const stats = report.stats || {};
      const kpis = el('div', 'health-kpis');
      for (const [lbl, val] of [
        ['活跃记忆', stats.active_memories ?? '—'],
        ['事件故事', stats.events_total ?? '—'],
        ['向量行', stats.vectors_total ?? '—'],
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
      schedHead.append(el('h3', '', '定期只读巡检设置'), pill('不删除/不合并任何记忆'));
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
      const saveSchedBtn = btn('保存巡检设置', 'sm');
      const schedMsg = el('small', '', '');
      saveSchedBtn.addEventListener('click', async () => {
        saveSchedBtn.disabled = true;
        try {
          const res = await reqJson(req, '/api/brain/health/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabledChk.checked, schedule: schedSel.value, idle_only: true }),
          });
          if (res.ok && res.data?.settings) {
            healthSettings = res.data.settings;
            schedMsg.textContent = '已保存设置';
          } else {
            schedMsg.textContent = res.data?.error || '保存失败';
          }
        } catch (err) {
          schedMsg.textContent = err?.message || '保存失败';
        } finally {
          saveSchedBtn.disabled = false;
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
          el('h3', '', `${chk.id ? `[${chk.id}] ` : ''}${chk.title || ''}`),
          pill(chk.status_label || chk.severity, chk.severity === 'good' ? 'good' : (chk.severity === 'error' ? 'danger' : 'warn')),
        );
        item.append(cHead, el('p', '', chk.impact || ''));

        const tech = el('details', 'tech-evidence');
        tech.append(el('summary', '', '查看技术依据、检查对象与样本明细'));
        const kv = el('div', 'health-kv');
        kv.append(
          el('div', '', `检查对象：${chk.object || 'brain.db'}`),
          el('div', '', `判断依据：${chk.basis || '—'}`),
          el('div', '', `数据来源：${chk.source || 'brain.db (?mode=ro)'}`),
          el('div', '', `可信度：${chk.confidence || '高可信'}`),
        );
        tech.append(kv);
        if (Array.isArray(chk.evidences) && chk.evidences.length) {
          const evList = el('ul', 'model-list');
          for (const ev of chk.evidences) {
            const li = el('li', '', `${ev.label || ''}: ${ev.note || ''}`);
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

      scanBtn.addEventListener('click', async () => {
        scanBtn.disabled = true;
        scanBtn.textContent = '巡检中…';
        try {
          const res = await reqJson(req, '/api/brain/health/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
          });
          if (!res.ok || !res.data?.report) {
            bannerMsg.textContent = res.data?.error || '巡检失败，请稍后重试。';
            return;
          }
          await drawHealth({ report: res.data.report, settings: healthSettings });
        } catch (err) {
          bannerMsg.textContent = err?.message || '巡检失败，请稍后重试。';
        } finally {
          scanBtn.disabled = false;
          scanBtn.textContent = '立即全面检查（只读）';
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

      wrap.append(banner, kpis, schedBar, checksWrap);
      host.replaceChildren(wrap);
    }

    const { data } = await reqJson(req, '/api/brain/health');
    await drawHealth(data || {});
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
