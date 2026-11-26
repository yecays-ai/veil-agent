/* Read-only workspace backed by the application's active Budget consumer. */
(() => {
  const categoryNames = {
    autonomy:'自主活动',glance:'瞄一眼',short_term_notes:'短期便签',delegate:'委派任务',
    experience_memory:'经历整理',recall_judge:'想起判官',calendar_agreements:'日历约定',
  };
  const currency = value => value == null ? '—' : `¥${value}`;
  window.renderWorkspaceBudget = function (host, request) {
    function create(tag, text) {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      return node;
    }
    const article = create('article');
    article.className = 'surface models-card';
    const heading = create('div');
    heading.className = 'section-heading';
    const refresh = create('button', '刷新预算');
    refresh.type = 'button';
    heading.append(create('h3', '预算总览'), refresh);
    const status = create('p');
    status.setAttribute('role', 'status');
    const content = create('div');
    article.append(heading, status, content);
    host.replaceChildren(article);
    async function load() {
      refresh.disabled = true;
      status.textContent = '正在读取本机账本…';
      content.replaceChildren();
      try {
        const response = await request('/api/budget/summary');
        if (!response.ok) throw new Error('budget unavailable');
        const data = await response.json();
        if (data?.schema_version !== 1 || !data.daily || !data.monthly || !Array.isArray(data.categories)) {
          throw new Error('budget payload unavailable');
        }
        status.textContent = data.ledger_unverified ? '账本存在待核实记录，付费请求已暂停。' : '只读 · 全部角色共用的费用账本';
        const list = create('ul');
        list.className = 'model-list';
        for (const [label, period] of [[`今日 ${data.day}`, data.daily], [`本月 ${data.month}`, data.monthly]]) {
          list.append(create('li', `${label}：已结算 ${currency(period.spent_cny)} ｜ 生效上限 ${currency(period.limit_cny)} ｜ 扣除预留后余额 ${currency(period.remaining_cny)}`));
        }
        list.append(create('li', `待核对预留：${currency(data.pending_cny)}（可跨日保留）`));
        const categories = create('ul');
        categories.className = 'model-list';
        for (const item of data.categories) {
          const label = categoryNames[item.id] || item.id;
          const cap = item.limit_cny == null ? '' : ` ｜ 分类日限额 ${currency(item.limit_cny)}`;
          categories.append(create('li', `${label}：已结算 ${currency(item.spent_cny)} ｜ 预留 ${currency(item.pending_cny)}${cap}`));
        }
        content.append(list, create('h4', '今日结算与预留 · 按账本分类'),
          create('p', '所列为可独立归类的项目；全部消费以总额为准。'), categories,
          create('h4', '今日已结算模型'), create('p', data.model_summary || '今日暂无已结算模型请求。'));
      } catch {
        status.textContent = '暂时无法读取预算账本，请检查本机连接后重试。';
      } finally {
        refresh.disabled = false;
      }
    }
    refresh.addEventListener('click', load);
    return load();
  };
})();
