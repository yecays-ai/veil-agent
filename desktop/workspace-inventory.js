/* #446 audit inventory view. Read-only, with no runtime configuration writes. */
(() => {
  'use strict';

  const PAGE_SIZE = 24;
  const ROUTE = '/api/config/inventory';
  const mounts = new WeakMap();
  const categories = {
    U: '用户常用', A: '高级参数', S: '敏感权限', R: '只读运行信息', P: '程序安全约束', X: '冗余废弃',
  };
  const evidence = {
    verified_runtime: '审计时已核实', source_default: '源码默认或解析结果',
    readable_unverified: '审计时未核实运行值', constant_not_applicable: '常量／不适用',
  };
  const detailFields = [
    ['key', '配置键'], ['storage', '存放位置（审计）'], ['source', '源码依据'],
    ['default', '默认值（审计）'], ['effective', '生效值记录（历史审计）'], ['range', '取值范围'],
    ['modify', '修改方式（审计时）'], ['precedence', '覆盖顺序'], ['timing', '生效时机'],
    ['persistence', '持久化／备份'], ['risk', '风险边界'], ['page', '归属页面（审计建议）'],
    ['effective_check', '证据说明'],
  ];
  const allFields = ['id', 'domain', 'module', 'item', 'category', 'effective_class', ...detailFields.map(([key]) => key)];
  const label = (labels, key) => Object.hasOwn(labels, key) ? labels[key] : key || '未分类';
  const normal = value => value.normalize('NFKC').toLocaleLowerCase();

  function element(document, tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeFilter(document, title, options) {
    const wrapper = element(document, 'label', `${title} `);
    const select = element(document, 'select');
    for (const [key, name] of [[ '', '全部' ], ...options]) {
      const option = element(document, 'option', name);
      option.value = key;
      select.append(option);
    }
    wrapper.append(select);
    return [wrapper, select];
  }

  function showRows(host, rows) {
    const document = host.ownerDocument;
    const badges = element(document, 'div');
    badges.className = 'audit-badges';
    const counts = rows.reduce((result, row) => result.set(row.category, (result.get(row.category) || 0) + 1), new Map());
    badges.append(element(document, 'strong', `${rows.length} 项`), element(document, 'span', '#446 历史审计 · 只读'));
    for (const [code, count] of counts) badges.append(element(document, 'span', `${label(categories, code)} ${count}`));
    const searchLabel = element(document, 'label', '搜索清单 ');
    const search = element(document, 'input');
    search.type = 'search';
    search.placeholder = '关键词、配置键、证据或编号';
    searchLabel.append(search);
    const options = (field, labels = {}) => [...new Set(rows.map(row => row[field]))].sort().map(key => [key, label(labels, key)]);
    const [domainLabel, domain] = makeFilter(document, '领域', options('domain'));
    const [categoryLabel, category] = makeFilter(document, '分类', options('category', categories));
    const [evidenceLabel, proof] = makeFilter(document, '生效证据', options('effective_class', evidence));
    const toolbar = element(document, 'div');
    toolbar.className = 'audit-toolbar';
    toolbar.append(searchLabel, domainLabel, categoryLabel, evidenceLabel);
    const status = element(document, 'p');
    status.className = 'audit-status';
    status.setAttribute('role', 'status');
    const table = element(document, 'table');
    table.className = 'audit-table';
    table.setAttribute('aria-label', '配置审计结果');
    const header = element(document, 'thead');
    const headings = element(document, 'tr');
    for (const title of ['配置项', '领域／模块', '分类', '证据']) {
      const cell = element(document, 'th', title);
      cell.scope = 'col';
      headings.append(cell);
    }
    header.append(headings);
    const list = element(document, 'tbody');
    table.append(header, list);
    const frame = element(document, 'div');
    frame.className = 'audit-table-frame';
    frame.append(table);
    const previous = element(document, 'button', '上一页');
    const next = element(document, 'button', '下一页');
    previous.type = next.type = 'button';
    const pagination = element(document, 'nav');
    pagination.setAttribute('aria-label', '审计清单分页');
    pagination.className = 'audit-pagination';
    pagination.append(previous, status, next);
    host.replaceChildren(badges, toolbar, frame, pagination);

    let page = 0;
    let filtered = rows;
    function renderPage() {
      const pageCount = Math.ceil(filtered.length / PAGE_SIZE);
      const first = page * PAGE_SIZE;
      const pageRows = filtered.slice(first, first + PAGE_SIZE);
      previous.disabled = page === 0;
      next.disabled = page + 1 >= pageCount;
      if (!filtered.length) {
        status.textContent = '没有符合条件的项目。';
        list.replaceChildren();
        return;
      }
      status.textContent = `匹配 ${filtered.length} 项 · 第 ${page + 1} / ${pageCount} 页 · 显示第 ${first + 1}–${first + pageRows.length} 项`;
      list.replaceChildren(...pageRows.map(row => {
        const record = element(document, 'tr');
        const item = element(document, 'td');
        const entry = element(document, 'details');
        const title = element(document, 'summary', `${row.id} · ${row.item}`);
        const fields = element(document, 'dl');
        for (const [key, name] of detailFields) {
          if (row[key]) fields.append(element(document, 'dt', name), element(document, 'dd', row[key]));
        }
        entry.append(title, fields);
        item.append(entry);
        record.append(item, element(document, 'td', `${row.domain} / ${row.module}`),
          element(document, 'td', label(categories, row.category)), element(document, 'td', label(evidence, row.effective_class)));
        return record;
      }));
      // Keep pagination keyboard focus inside the available controls.
      if (document.activeElement === previous && previous.disabled && !next.disabled) next.focus();
      if (document.activeElement === next && next.disabled && !previous.disabled) previous.focus();
    }
    function applyFilters() {
      const words = normal(search.value).trim().split(/\s+/u).filter(Boolean);
      filtered = rows.filter(row => (!domain.value || row.domain === domain.value)
        && (!category.value || row.category === category.value)
        && (!proof.value || row.effective_class === proof.value)
        && words.every(word => row.searchText.includes(word)));
      page = 0;
      renderPage();
    }
    search.addEventListener('input', applyFilters);
    for (const select of [domain, category, proof]) select.addEventListener('change', applyFilters);
    previous.addEventListener('click', () => { if (page > 0) { page -= 1; renderPage(); } });
    next.addEventListener('click', () => { if (!next.disabled) { page += 1; renderPage(); } });
    renderPage();
  }

  window.renderWorkspaceInventory = function renderWorkspaceInventory(host) {
    const existing = mounts.get(host);
    if (existing && host.contains(existing.marker)) return existing.promise;
    const document = host.ownerDocument;
    const marker = element(document, 'section');
    marker.className = 'audit-inventory';
    const status = element(document, 'p', '正在读取 #446 审计清单…');
    status.setAttribute('role', 'status');
    marker.append(status);
    host.replaceChildren(marker);
    // Media/permissions and generic filesystem access stay outside this module.
    const promise = (async () => {
      try {
        const response = await window.veilDesktop.request(ROUTE);
        const data = response?.data;
        if (!response?.ok || data?.schema_version !== 1 || !Array.isArray(data.rows) || !data.rows.length
            || !data.rows.every(row => row && allFields.every(key => typeof row[key] === 'string'))) throw new Error();
        if (host.contains(marker)) showRows(marker, data.rows.map(row => ({
          ...row,
          searchText: normal([...allFields.map(key => row[key]), label(categories, row.category), label(evidence, row.effective_class)].join(' ')),
        })));
      } catch {
        if (!host.contains(marker)) return;
        status.className = 'audit-status workspace-inline-error';
        status.setAttribute('role', 'alert');
        status.textContent = '无法读取 #446 审计清单。请确认桌面与后端版本一致，然后重试。';
        const retry = element(document, 'button', '重试');
        retry.type = 'button';
        retry.addEventListener('click', () => {
          mounts.delete(host);
          window.renderWorkspaceInventory(host);
        });
        marker.append(retry);
      }
    })();
    mounts.set(host, { marker, promise });
    return promise;
  };
})();
