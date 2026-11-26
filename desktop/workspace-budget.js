/* Editable Budget Workspace backed by /api/budget/summary and /api/models limits. */
(() => {
  const categoryNames = {
    autonomy: '自主活动',
    glance: '瞄一眼',
    short_term_notes: '短期便签',
    delegate: '委派任务',
    experience_memory: '经历整理',
    recall_judge: '想起判官',
    calendar_agreements: '日历约定',
  };
  const currency = value => (value == null ? '—' : `¥${value}`);

  function create(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function meterRatio(spent, limit) {
    const s = Number(spent);
    const l = Number(limit);
    if (!Number.isFinite(s) || !Number.isFinite(l) || l <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((s / l) * 100)));
  }

  window.renderWorkspaceBudget = function (host, request) {
    const root = create('div', 'workspace-content');

    // Summary & editable controls header card
    const summaryCard = create('article', 'veil-card');
    const head = create('div', 'veil-card-head');
    const titleWrap = create('div');
    const title = create('h3', '', '预算账本与消费上限');
    const status = create('small', '', '正在读取本机账本…');
    status.setAttribute('role', 'status');
    titleWrap.append(title, status);
    const refresh = create('button', 'veil-btn sm', '刷新数据');
    refresh.type = 'button';
    head.append(titleWrap, refresh);

    const kpiGrid = create('div', 'kpi-grid');
    kpiGrid.style.marginBottom = '14px';

    const editForm = create('form', 'filter-bar');
    editForm.style.padding = '12px 14px';
    editForm.style.borderRadius = '12px';
    editForm.style.background = 'var(--surface-muted)';
    editForm.style.border = '1px solid var(--veil-rule)';

    const dailyLbl = create('label', '', '每日消费上限 ¥');
    const dailyInput = document.createElement('input');
    dailyInput.type = 'number';
    dailyInput.min = '1';
    dailyInput.max = '1000';
    dailyInput.step = '1';
    dailyInput.style.width = '96px';
    dailyLbl.append(dailyInput);

    const monthlyLbl = create('label', '', '每月消费上限 ¥');
    const monthlyInput = document.createElement('input');
    monthlyInput.type = 'number';
    monthlyInput.min = '1';
    monthlyInput.max = '30000';
    monthlyInput.step = '1';
    monthlyInput.style.width = '110px';
    monthlyLbl.append(monthlyInput);

    const saveBtn = create('button', 'veil-btn primary sm workspace-budget-save', '保存预算上限');
    saveBtn.type = 'submit';
    const saveNote = create('small', '', '');
    editForm.append(dailyLbl, monthlyLbl, saveBtn, saveNote);
    summaryCard.append(head, kpiGrid, editForm);

    // Categories & model usage card
    const catCard = create('article', 'veil-card');
    const catHead = create('div', 'veil-card-head');
    catHead.append(create('h3', '', '今日分项结算与模型明细'));
    const catBody = create('div', 'workspace-content');
    catCard.append(catHead, catBody);

    root.append(summaryCard, catCard);
    host.replaceChildren(root);

    async function load() {
      refresh.disabled = true;
      status.textContent = '正在读取本机账本…';
      try {
        const response = await request('/api/budget/summary');
        if (!response.ok) throw new Error('budget unavailable');
        const data = await response.json();
        if (data?.schema_version !== 1 || !data.daily || !data.monthly || !Array.isArray(data.categories)) {
          throw new Error('budget payload unavailable');
        }
        status.textContent = data.ledger_unverified
          ? '账本存在待核实记录，付费请求已暂停'
          : '全部角色共用实时账本 · 修改限额后即时生效';

        if (data.daily.limit_cny != null && !dailyInput.value) dailyInput.value = String(data.daily.limit_cny);
        if (data.monthly.limit_cny != null && !monthlyInput.value) monthlyInput.value = String(data.monthly.limit_cny);

        kpiGrid.replaceChildren();
        for (const [label, spent, limit, rem] of [
          [`今日结算 (${data.day})`, data.daily.spent_cny, data.daily.limit_cny, data.daily.remaining_cny],
          [`本月结算 (${data.month})`, data.monthly.spent_cny, data.monthly.limit_cny, data.monthly.remaining_cny],
          ['待核对预留', data.pending_cny, null, null],
        ]) {
          const card = create('div', 'veil-card');
          card.style.padding = '12px 14px';
          card.append(
            create('div', 'kpi-top', label),
            create('div', 'kpi-val', currency(spent)),
            create('div', 'kpi-sub', limit != null ? `上限 ${currency(limit)} · 余额 ${currency(rem)}` : '跨日自动结算'),
          );
          if (limit != null) {
            const meter = create('div', 'budget-meter');
            const fill = create('div', 'budget-seg-mem');
            fill.style.width = `${meterRatio(spent, limit)}%`;
            meter.append(fill);
            card.append(meter);
          }
          kpiGrid.append(card);
        }

        catBody.replaceChildren();
        const grid = create('div', 'layer-list');
        if (data.categories.length) {
          for (const item of data.categories) {
            const box = create('div', 'layer-item');
            const top = create('div', 'layer-item-top');
            top.append(create('strong', '', categoryNames[item.id] || item.id), create('span', 'veil-pill', currency(item.spent_cny)));
            const sub = item.limit_cny != null
              ? `预留 ${currency(item.pending_cny)} · 分类日上限 ${currency(item.limit_cny)}`
              : `预留 ${currency(item.pending_cny)}`;
            box.append(top, create('small', '', sub));
            grid.append(box);
          }
          catBody.append(grid);
        } else {
          catBody.append(create('p', '', '今日暂无分类结算明细，全部消费以总账本为准。'));
        }
        catBody.append(create('h4', '', '今日已结算模型'), create('p', '', data.model_summary || '今日暂无已结算模型请求。'));
      } catch {
        status.textContent = '暂时无法读取预算账本，请检查本机连接后重试。';
      } finally {
        refresh.disabled = false;
      }
    }

    editForm.addEventListener('submit', async event => {
      event.preventDefault();
      saveBtn.disabled = true;
      saveNote.textContent = '正在保存…';
      try {
        const res = await request('/api/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ limits: { daily_cny: dailyInput.value, monthly_cny: monthlyInput.value } }),
        });
        if (!res.ok) throw new Error('保存失败');
        const domDaily = document.getElementById('limit-daily');
        const domMonthly = document.getElementById('limit-monthly');
        if (domDaily) domDaily.value = dailyInput.value;
        if (domMonthly) domMonthly.value = monthlyInput.value;
        saveNote.textContent = '预算上限已保存';
        await load();
      } catch (err) {
        saveNote.textContent = err.message || '保存失败';
      } finally {
        saveBtn.disabled = false;
      }
    });

    refresh.addEventListener('click', load);
    return load();
  };
})();
