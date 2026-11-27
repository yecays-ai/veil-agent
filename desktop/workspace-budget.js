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
    cognitive_consolidation: '认知巩固',
  };
  const verificationReasons = new Map(Object.entries({
    budget: '额度不足', budget_exhausted: '额度不足', timeout: '核验超时', contradicted: '原话反驳',
    insufficient: '证据不足', invalid_response: '核验返回无效', provider_error: '模型请求失败',
    sensitive_payload: '材料含疑似凭据，未发送', invalid_payload: '核验材料无效', payload_too_large: '核验材料超长', invalid_usage: '模型用量无法核对',
    request_not_sent: '请求未发送', stale: '原始证据已变化', stale_ref: '证据引用失效',
    unavailable: '核验暂不可用', interrupted: '核验中断', accounting: '费用尚未核对',
  }));
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
    dailyInput.min = '0.01';
    dailyInput.max = '1000';
    dailyInput.step = 'any';
    dailyInput.style.width = '96px';
    dailyLbl.append(dailyInput);

    const monthlyLbl = create('label', '', '每月消费上限 ¥');
    const monthlyInput = document.createElement('input');
    monthlyInput.type = 'number';
    monthlyInput.min = '0.01';
    monthlyInput.max = '30000';
    monthlyInput.step = 'any';
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

    const semanticCard = create('article', 'veil-card');
    semanticCard.hidden = true;
    const semanticTitle = create('h3', '', '关系语义核验 · 当前角色');
    const semanticCost = create('p');
    const semanticCounts = create('p');
    const semanticReasons = create('ul');
    const semanticForm = create('form', 'filter-bar');
    const capLabel = create('label', '', '语义日额度 ¥（0 暂停）');
    const capInput = create('input');
    Object.assign(capInput, { type: 'number', min: '0', max: '1000', step: '0.00000001', required: true });
    capLabel.append(capInput);
    const timeoutLabel = create('label', '', '单次超时（秒）');
    const timeoutInput = create('input');
    Object.assign(timeoutInput, { type: 'number', min: '1', max: '60', step: '1', required: true });
    timeoutLabel.append(timeoutInput);
    const semanticSave = create('button', 'veil-btn primary sm', '保存语义核验设置');
    semanticSave.type = 'submit';
    const semanticNote = create('small');
    semanticNote.setAttribute('role', 'status');
    semanticForm.append(capLabel, timeoutLabel, semanticSave, semanticNote);
    semanticCard.append(semanticTitle, semanticCost, semanticCounts, semanticReasons, semanticForm);

    const consCard = create('article', 'veil-card');
    consCard.hidden = true;
    const consTitle = create('h3', '', '后台认知巩固 · 当前角色（默认关闭灰度）');
    const consCost = create('p');
    const consCounts = create('p');
    const consForm = create('form', 'filter-bar');
    const consEnLabel = create('label', '', '启用后台巩固 ');
    const consEnInput = document.createElement('input');
    consEnInput.type = 'checkbox';
    consEnLabel.append(consEnInput);
    const consCapLabel = create('label', '', '巩固日上限 ¥（0 暂停）');
    const consCapInput = document.createElement('input');
    Object.assign(consCapInput, { type: 'number', min: '0', max: '5', step: '0.01', required: true });
    consCapLabel.append(consCapInput);
    const consIntervalLabel = create('label', '', '间隔（小时）');
    const consIntervalInput = document.createElement('input');
    Object.assign(consIntervalInput, { type: 'number', min: '1', max: '168', step: '1', required: true });
    consIntervalLabel.append(consIntervalInput);
    const consMaxLabel = create('label', '', '每日最多次数');
    const consMaxInput = document.createElement('input');
    Object.assign(consMaxInput, { type: 'number', min: '1', max: '12', step: '1', required: true });
    consMaxLabel.append(consMaxInput);
    const consSave = create('button', 'veil-btn primary sm', '保存巩固预算与频率');
    consSave.type = 'submit';
    const consNote = create('small');
    consNote.setAttribute('role', 'status');
    consForm.append(consEnLabel, consCapLabel, consIntervalLabel, consMaxLabel, consSave, consNote);
    consCard.append(consTitle, consCost, consCounts, consForm);

    root.append(summaryCard, semanticCard, consCard, catCard);
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

        // limit_cny includes today's approved raise; the editable field must show the base limit,
        // otherwise saving an untouched form folds the raise into the base and inflates the cap.
        const dailyBase = data.daily.base_limit_cny ?? data.daily.limit_cny;
        const monthlyBase = data.monthly.base_limit_cny ?? data.monthly.limit_cny;
        if (dailyBase != null) dailyInput.value = String(dailyBase);
        if (monthlyBase != null) monthlyInput.value = String(monthlyBase);

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

        const semantic = data.relationship_semantic;
        semanticCard.hidden = !semantic;
        if (semantic) {
          capInput.value = semantic.daily_cap_cny;
          timeoutInput.value = String(semantic.timeout_seconds);
          const paused = Number(semantic.daily_cap_cny) === 0 ? ' · 核验已暂停' : '';
          semanticCost.textContent = '今日已用 ' + currency(semantic.spent_cny)
            + ' · 预留 ' + currency(semantic.pending_cny) + ' · 额度余额 '
            + currency(semantic.remaining_cny) + paused;
          const counts = semantic.verification;
          semanticCounts.textContent = counts
            ? '待核验 ' + counts.pending + ' 条 · 已晋升 ' + counts.promoted + ' 条'
            : '当前角色的核验状态暂不可读。';
          semanticReasons.replaceChildren();
          if (counts) {
            const reasons = Object.entries(counts.reasons || {})
              .filter(([key]) => !['pending', 'dispatched', 'supported'].includes(key));
            for (const [key, count] of reasons) {
              semanticReasons.append(create('li', '', (verificationReasons.get(key) || key) + '：' + count + ' 条'));
            }
            if (!reasons.length) semanticReasons.append(create('li', '', '暂无失败或未通过原因。'));
          }
        }

        const cons = data.cognitive_consolidation;
        consCard.hidden = !cons;
        if (cons) {
          consEnInput.checked = Boolean(cons.enabled);
          consCapInput.value = String(cons.daily_cap_cny ?? '0.05');
          consIntervalInput.value = String(cons.interval_hours ?? 24);
          consMaxInput.value = String(cons.max_runs_per_day ?? 2);
          consCost.textContent = '今日已用 ' + currency(cons.spent_cny)
            + ' · 预留 ' + currency(cons.pending_cny) + ' · 额度余额 '
            + currency(cons.remaining_cny) + ' · ' + (cons.status_text || (cons.enabled ? '已启用' : '已关闭'));
          consCounts.textContent = '今日运行 ' + (cons.runs_today ?? 0) + '/' + (cons.max_runs_per_day ?? 2)
            + ' 次 · 累计认知新增 ' + (cons.cognition_written ?? 0)
            + ' / 修订 ' + (cons.cognition_revised ?? 0)
            + ' · 重要度建议 ' + (cons.importance_proposed ?? 0);
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

    semanticForm.addEventListener('submit', async event => {
      event.preventDefault();
      semanticSave.disabled = true;
      semanticNote.textContent = '正在保存…';
      try {
        const res = await request('/api/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ semantic_verification: {
            daily_cap_cny: capInput.value, timeout_seconds: Number(timeoutInput.value),
          } }),
        });
        if (!res.ok) {
          const error = await res.json().catch(() => null);
          throw new Error(error?.message || '保存失败');
        }
        semanticNote.textContent = '已保存，下次核验生效，无需重启。';
        await load();
      } catch (err) {
        semanticNote.textContent = err.message || '保存失败';
      } finally {
        semanticSave.disabled = false;
      }
    });

    consForm.addEventListener('submit', async event => {
      event.preventDefault();
      consSave.disabled = true;
      consNote.textContent = '正在保存…';
      try {
        const res = await request('/api/brain/consolidation/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            values: {
              enabled: Boolean(consEnInput.checked),
              daily_budget_cny: Number(consCapInput.value),
              interval_hours: Number(consIntervalInput.value),
              max_runs_per_day: Number(consMaxInput.value),
            },
          }),
        });
        const payload = await res.json().catch(() => null);
        if (!res.ok || !payload?.ok) {
          throw new Error(payload?.error || '保存失败');
        }
        consNote.textContent = '已保存并持久化，下次巩固周期生效，无需重启。';
        await load();
      } catch (err) {
        consNote.textContent = err.message || '保存失败';
      } finally {
        consSave.disabled = false;
      }
    });

    refresh.addEventListener('click', load);
    return load();
  };
})();
