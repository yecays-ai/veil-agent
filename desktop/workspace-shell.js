/* ==========================================================================
   VEIL Unified Workspace Shell (Stage 3 Production)
   - Three workspace groups (日常 / VEIL BRAIN / 管理) using real DOM nodes
   - Nocturne curved Level-2 subnav with smooth indicator animation
   - 4-theme switcher (rose / gold / blue / night) + detach view trigger
   - Activity control cards + timeline layout + scroll jump helpers
   - Budget, #446 Inventory, Workbench & Safety workspaces (strictly safe DOM)
   ========================================================================== */
(() => {
  'use strict';

  const CUSTOM_PAGES = new Set([
    'brain-home',
    'workspace-events',
    'brain-cognition',
    'brain-recall',
    'brain-health',
    'workspace-home',
    'workspace-work',
    'workspace-budget',
    'workspace-inventory',
    'workspace-access',
  ]);

  const BRAIN_TABS = [
    { page: 'brain-home', label: '记忆概览' },
    { page: 'memory', label: '记忆库' },
    { page: 'workspace-events', label: '事件故事' },
    { page: 'brain-cognition', label: '关系与成长' },
    { page: 'brain-recall', label: '召回诊断' },
    { page: 'brain-health', label: '健康检查', dotId: 'subnav-health-dot' },
  ];

  const CONFIG_TABS = [
    { page: 'more', label: '运行与外观' },
    { page: 'instructions', label: '指令与角色' },
    { page: 'context', label: '实时上下文' },
    { page: 'workspace-inventory', label: '全量配置清单' },
    { page: 'diagnostics', label: '系统诊断' },
  ];

  const PAGE_META = {
    chat: { crumb: '日常 · 对话', title: '聊天对话', detach: 'chat' },
    life: { crumb: '日常 · 记录', title: '生活记录与日历', detach: 'life' },
    activity: { crumb: '日常 · 节律', title: '自主活动与伴随', detach: 'activity' },
    'workspace-home': { crumb: '日常 · 总览', title: 'VEIL 工作台' },
    'brain-home': { crumb: 'VEIL Brain · 概览', title: '记忆系统总览' },
    memory: { crumb: 'VEIL Brain · 记忆库', title: '统一记忆库', detach: 'memory' },
    'workspace-events': { crumb: 'VEIL Brain · 事件故事', title: '共同经历与时间线' },
    'brain-cognition': { crumb: 'VEIL Brain · 关系与认知', title: '关系底座与习得认知', detach: 'peter' },
    'brain-recall': { crumb: 'VEIL Brain · 召回诊断', title: '记忆检索与注入链路' },
    'brain-health': { crumb: 'VEIL Brain · 健康中心', title: '只读记忆健康检查' },
    peter: { crumb: 'VEIL Brain · 角色档案', title: 'Peter 角色与关系页', detach: 'peter' },
    mcp: { crumb: '管理 · 工具接入', title: 'MCP 与工具权限', detach: 'mcp' },
    context: { crumb: '管理 · 统一配置', title: '上下文层与共享文件', detach: 'context' },
    instructions: { crumb: '管理 · 统一配置', title: '系统指令与角色准则', detach: 'instructions' },
    more: { crumb: '管理 · 统一配置', title: '模型、外观与系统设置', detach: 'more' },
    diagnostics: { crumb: '管理 · 诊断证据', title: '原始请求快照与追踪', detach: 'diagnostics' },
    'workspace-work': { crumb: '管理 · 工作区', title: '开发工作区与 GitHub' },
    'workspace-budget': { crumb: '管理 · 账本预算', title: '实时费用与分类预算' },
    'workspace-inventory': { crumb: '管理 · 全量清单', title: '#446 668 项配置审计清单' },
    'workspace-access': { crumb: '管理 · 安全边界', title: '权限与安全只读核对' },
  };

  let initialized = false;
  let currentPage = 'chat';
  let customSections = new Map();
  let navExtButtons = new Map();
  let activityLayoutBuilt = false;
  let jumpRoot = null;
  let jumpUp = null;
  let jumpDown = null;
  let requestFn = null;
  let nativeSetPageFn = null;
  let indicatorRaf = 0;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function applyVisualThemeState(visualTheme, broadcastToMain = false) {
    const themeId = ['rose', 'gold', 'blue', 'night'].includes(visualTheme) ? visualTheme : 'rose';
    const root = document.documentElement;
    root.dataset.visualTheme = themeId;
    root.dataset.theme = themeId === 'night' ? 'dark' : 'light';
    for (const btn of document.querySelectorAll('[data-visual-theme-pick]')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.visualThemePick === themeId));
    }
    const modeSelect = document.getElementById('theme-mode');
    if (modeSelect && ['rose', 'gold', 'blue', 'night', 'light', 'dark'].includes(modeSelect.value)) {
      modeSelect.dataset.syncingTheme = 'true';
      modeSelect.value = themeId === 'night' ? 'dark' : 'light';
      delete modeSelect.dataset.syncingTheme;
    }
    if (broadcastToMain && window.veilDesktop?.saveTheme) {
      window.veilDesktop.saveTheme(themeId).catch(() => {});
    }
  }

  function initThemePills() {
    for (const btn of document.querySelectorAll('[data-visual-theme-pick]')) {
      btn.addEventListener('click', () => {
        applyVisualThemeState(btn.dataset.visualThemePick, true);
      });
    }
    window.veilDesktop?.getVisualTheme?.().then(res => {
      if (res?.visualTheme) applyVisualThemeState(res.visualTheme, false);
      else if (document.documentElement.dataset.theme === 'dark') applyVisualThemeState('night', false);
      else applyVisualThemeState('rose', false);
    }).catch(() => {});
    window.veilDesktop?.onThemeChanged?.(payload => {
      if (payload?.visualTheme) applyVisualThemeState(payload.visualTheme, false);
      else if (payload?.theme) applyVisualThemeState(payload.theme === 'dark' ? 'night' : 'rose', false);
    });
  }

  function subnavTabsFor(page) {
    if (BRAIN_TABS.some(t => t.page === page)) return BRAIN_TABS;
    if (CONFIG_TABS.some(t => t.page === page)) return CONFIG_TABS;
    return null;
  }

  function positionIndicator(track, indicator, activeTab) {
    if (!track || !indicator || !activeTab) {
      if (indicator) indicator.style.opacity = '0';
      return;
    }
    cancelAnimationFrame(indicatorRaf);
    indicatorRaf = requestAnimationFrame(() => {
      const tr = track.getBoundingClientRect();
      const ar = activeTab.getBoundingClientRect();
      if (!ar.width) {
        indicator.style.opacity = '0';
        return;
      }
      indicator.style.opacity = '1';
      indicator.style.width = `${Math.round(ar.width)}px`;
      indicator.style.transform = `translateX(${Math.round(ar.left - tr.left - 4)}px)`;
    });
  }

  function updateSubnav(page) {
    const shell = document.getElementById('subnav-shell');
    const track = document.getElementById('subnav-track');
    const indicator = document.getElementById('subnav-indicator');
    const crumb = document.getElementById('topbar-crumb');
    const detachBtn = document.getElementById('detach-current-btn');
    const meta = PAGE_META[page] || { crumb: 'VEIL', title: page };
    if (crumb) crumb.textContent = meta.crumb;
    if (detachBtn) {
      const targetDetach = meta.detach || '';
      detachBtn.hidden = !targetDetach || document.body.classList.contains('detached-window');
      detachBtn.dataset.detachTarget = targetDetach;
    }
    if (!shell || !track) return;
    const tabs = subnavTabsFor(page);
    if (!tabs || document.body.classList.contains('detached-window')) {
      shell.hidden = true;
      return;
    }
    shell.hidden = false;
    track.querySelectorAll('.subnav-tab').forEach(node => node.remove());
    let activeNode = null;
    for (const tab of tabs) {
      const btn = el('button', 'subnav-tab', tab.label);
      btn.type = 'button';
      btn.dataset.subnavPage = tab.page;
      const selected = tab.page === page;
      btn.setAttribute('aria-selected', String(selected));
      if (tab.dotId) {
        const dot = el('span', 'subnav-dot');
        dot.id = tab.dotId;
        dot.hidden = !window.veilBrain?.hasHealthWarnings?.();
        btn.append(dot);
      }
      btn.addEventListener('click', () => go(tab.page));
      track.append(btn);
      if (selected) activeNode = btn;
    }
    positionIndicator(track, indicator, activeNode);
  }

  function ensureCustomSection(page) {
    if (customSections.has(page)) return customSections.get(page);
    const parent = document.getElementById('log')?.parentElement || document.querySelector('.workspace') || document.body;
    const section = el('section', 'page workspace-view');
    section.id = `${page}-view`;
    section.hidden = true;
    const meta = PAGE_META[page] || { crumb: 'VEIL', title: page };
    const head = el('div', 'workspace-heading');
    head.append(el('p', 'workspace-kicker', meta.crumb), el('h2', '', meta.title));
    const host = el('div', 'workspace-content');
    section.append(head, host);
    parent.append(section);
    customSections.set(page, { section, host });
    return customSections.get(page);
  }

  function createExtNavButton(page, iconName, label) {
    const btn = el('button', 'nav-item workspace-nav-item');
    btn.type = 'button';
    btn.dataset.workspacePage = page;
    btn.setAttribute('aria-label', label);
    const iconSpan = el('span', 'nav-icon');
    if (typeof window.veilIcon === 'function') iconSpan.innerHTML = window.veilIcon(iconName, 17);
    const textSpan = el('span', 'nav-label', label);
    btn.append(iconSpan, textSpan);
    btn.addEventListener('click', () => go(page));
    navExtButtons.set(page, btn);
    return btn;
  }

  function reorganizeSidebar() {
    const nav = document.getElementById('navigation') || document.querySelector('.navigation');
    if (!nav || nav.dataset.workspaceGrouped === 'true') return;
    nav.dataset.workspaceGrouped = 'true';
    const person = nav.querySelector('.sidebar-person');
    const foot = nav.querySelector('.sidebar-foot');
    const existing = new Map();
    for (const btn of nav.querySelectorAll('.nav-item[data-page]')) {
      existing.set(btn.dataset.page, btn);
    }
    const brand = el('div', 'workspace-brand', 'VEIL');
    nav.prepend(brand);

    const makeGroup = (titleText) => {
      const wrap = el('div', 'workspace-nav-group');
      wrap.append(el('div', 'workspace-nav-title', titleText));
      return wrap;
    };

    const gDaily = makeGroup('日常');
    const gBrain = makeGroup('VEIL BRAIN');
    const gAdmin = makeGroup('管理');

    const take = (key, newLabel, newIcon) => {
      const node = existing.get(key);
      if (!node) return null;
      if (newLabel) {
        const lbl = node.querySelector('.nav-label') || node.querySelector('span:last-child');
        if (lbl) lbl.textContent = newLabel;
      }
      if (newIcon && typeof window.veilIcon === 'function') {
        const iconHolder = node.querySelector('[data-lucide]') || node.querySelector('.nav-icon') || node.firstElementChild;
        if (iconHolder) {
          iconHolder.dataset.lucide = newIcon;
          iconHolder.innerHTML = window.veilIcon(newIcon, 17);
        }
      }
      return node;
    };

    // Group 1: 日常
    for (const item of [
      take('chat', '聊天'),
      createExtNavButton('workspace-home', 'layout-dashboard', '工作台'),
      take('life', '生活记录'),
      take('activity', '自主活动'),
    ]) {
      if (item) gDaily.append(item);
    }

    // Group 2: VEIL BRAIN
    for (const item of [
      createExtNavButton('brain-home', 'radar', '记忆概览'),
      take('memory', '记忆库', 'book-open'),
      createExtNavButton('workspace-events', 'calendar', '事件故事'),
      createExtNavButton('brain-cognition', 'sparkles', '关系成长'),
      createExtNavButton('brain-recall', 'stethoscope', '召回与快照'),
      createExtNavButton('brain-health', 'shield', '健康检查'),
      take('peter', 'Peter 档案', 'user'),
    ]) {
      if (item) gBrain.append(item);
    }

    // Group 3: 管理 (context / diagnostics remain in DOM for tests & accessible via Unified Config tabs, hidden from sidebar clutter)
    const ctxBtn = take('context', '上下文层');
    const diagBtn = take('diagnostics', '原始诊断');
    if (ctxBtn) ctxBtn.hidden = true;
    if (diagBtn) diagBtn.hidden = true;
    for (const item of [
      createExtNavButton('workspace-work', 'briefcase', '开发工作区'),
      createExtNavButton('workspace-budget', 'wallet', '账本预算'),
      take('mcp', '工具与 MCP'),
      take('instructions', '指令集', 'list-tree'),
      createExtNavButton('workspace-inventory', 'clipboard-list', '配置清单'),
      take('more', '统一配置', 'sliders-horizontal'),
      ctxBtn,
      diagBtn,
    ]) {
      if (item) gAdmin.append(item);
    }

    nav.replaceChildren(...[person, brand, gDaily, gBrain, gAdmin, foot].filter(Boolean));
  }

  function upgradeActivityLayout() {
    const view = document.getElementById('activity-view');
    if (!view || activityLayoutBuilt) return;
    activityLayoutBuilt = true;
    const overviewCards = [...view.querySelectorAll('.activity-overview')];
    const timeline = view.querySelector('.activity-timeline');
    const filters = view.querySelector('.activity-filters');
    if (!overviewCards.length || !timeline) return;

    const metricsGrid = el('div', 'workspace-activity-metrics');
    for (const card of overviewCards) {
      card.classList.add('workspace-activity-metric');
      const toggleBtn = card.querySelector('button');
      if (toggleBtn) {
        toggleBtn.classList.add('workspace-switch');
        toggleBtn.setAttribute('role', 'switch');
        const syncChecked = () => {
          const on = !/关|停|pause|off/i.test(toggleBtn.textContent || '') && !card.classList.contains('is-off');
          toggleBtn.setAttribute('aria-checked', String(on));
        };
        syncChecked();
        const obs = new MutationObserver(syncChecked);
        obs.observe(toggleBtn, { childList: true, characterData: true, subtree: true });
      }
      metricsGrid.append(card);
    }

    const feedGrid = el('div', 'workspace-feed-grid');
    const feedCard = el('article', 'surface workspace-feed');
    feedCard.append(el('h3', '', '近期轨迹'));
    if (filters) feedCard.append(filters);
    feedCard.append(timeline);

    const aside = el('aside', 'workspace-activity-aside');
    const statusCard = el('article', 'surface workspace-activity-service');
    statusCard.append(el('h3', '', '运行说明'));
    statusCard.append(el('p', 'veil-soft-note', '自主活动开启时，Peter 会按节律唤醒整理经历、反思并与你轻声报平安。所有外部动作仍受安全与费用预算上限约束。'));
    const btnRow = el('div', 'workspace-links');
    const budgetShortcut = el('button', 'workspace-link', '查看今日预算');
    budgetShortcut.type = 'button';
    budgetShortcut.addEventListener('click', () => go('workspace-budget'));
    const brainShortcut = el('button', 'workspace-link', '查看记忆健康');
    brainShortcut.type = 'button';
    brainShortcut.addEventListener('click', () => go('brain-health'));
    btnRow.append(budgetShortcut, brainShortcut);
    statusCard.append(btnRow);
    aside.append(statusCard);

    feedGrid.append(feedCard, aside);
    const heading = view.querySelector('.page-head') || view.querySelector('h2')?.parentElement;
    if (heading) heading.classList.add('workspace-activity-heading');
    view.append(metricsGrid, feedGrid);
  }

  async function renderWorkspaceHome(host) {
    host.replaceChildren();
    const hero = el('div', 'workspace-hero');
    hero.append(el('div', 'workspace-kicker', 'VEIL WORKSPACE'));
    hero.append(el('h3', '', '今天也在一起认真生活与工作'));
    hero.append(el('p', '', '快速进入对话、VEIL Brain 记忆管理中心、实时预算账本与全量配置清单。'));
    const links = el('div', 'workspace-links');
    for (const [label, target] of [
      ['进入对话', 'chat'],
      ['记忆概览', 'brain-home'],
      ['共同经历', 'workspace-events'],
      ['记忆健康检查', 'brain-health'],
      ['实时预算账本', 'workspace-budget'],
    ]) {
      const btn = el('button', 'workspace-link', label);
      btn.type = 'button';
      btn.addEventListener('click', () => go(target));
      links.append(btn);
    }
    hero.append(links);

    const grid = el('div', 'workspace-grid');
    const cards = [
      { title: '记忆系统 (P4)', desc: '记忆库分页检索、版本 Diff 比对、事件故事与关系底座。', action: '打开记忆库', page: 'memory' },
      { title: '记忆健康 (P5)', desc: '9 项确定性只读巡检，不改动也不删减任何私密记忆。', action: '查看健康报告', page: 'brain-health' },
      { title: '召回链路诊断', desc: '追踪每一轮记忆检索分数、Token 预算与真实开销。', action: '打开召回诊断', page: 'brain-recall' },
      { title: '#446 全量配置清单', desc: '668 项历史审计配置按领域、分类与生效证据只读浏览。', action: '查看配置清单', page: 'workspace-inventory' },
    ];
    for (const item of cards) {
      const card = el('article', 'surface workspace-card');
      card.append(el('h3', '', item.title), el('p', '', item.desc));
      const b = el('button', 'veil-btn', item.action);
      b.type = 'button';
      b.addEventListener('click', () => go(item.page));
      card.append(b);
      grid.append(card);
    }
    host.append(hero, grid);
  }

  async function renderWorkspaceWork(host, req) {
    host.replaceChildren(el('p', '', '正在加载开发工作区与系统权限配置…'));

    const grid = el('div', 'overview-grid');

    // Card 1: Workbench toggle & project folders
    const wbCard = el('article', 'veil-card');
    const wbHead = el('div', 'veil-card-head');
    wbHead.append(el('h3', '', '开发工作区与本地项目目录'));
    const toggleWrap = el('div', 'segmented');
    const onBtn = el('button', '', '开启工作区');
    const offBtn = el('button', '', '关闭');
    onBtn.type = offBtn.type = 'button';
    toggleWrap.append(onBtn, offBtn);
    wbHead.append(toggleWrap);

    const folderList = el('div', 'workspace-content');
    folderList.style.marginBottom = '12px';
    const wbActions = el('div', 'workspace-links');
    const addFolderBtn = el('button', 'veil-btn primary sm', '+ 添加项目文件夹');
    addFolderBtn.type = 'button';
    const wbStatus = el('small', '', '');
    wbActions.append(addFolderBtn, wbStatus);
    wbCard.append(wbHead, el('p', '', '允许 Peter 在指定项目目录中读写代码、查看差异并执行测试。'), folderList, wbActions);

    // Card 2: Allowed GitHub / Web Hosts + Quick Capabilities
    const rightCol = el('div', 'workspace-content');
    const hostCard = el('article', 'veil-card');
    hostCard.append(el('h3', '', 'GitHub 与网站访问白名单'));
    const hostList = el('div', 'workspace-links');
    const hostForm = el('form', 'filter-bar');
    const hostInput = document.createElement('input');
    hostInput.type = 'text';
    hostInput.placeholder = '例如 github.com 或 api.github.com';
    hostInput.style.flex = '1';
    const addHostBtn = el('button', 'veil-btn sm', '添加网站');
    addHostBtn.type = 'submit';
    hostForm.append(hostInput, addHostBtn);
    hostCard.append(hostList, hostForm);

    const appsCard = el('article', 'veil-card');
    appsCard.append(el('h3', '', '系统工具、软件与边界联动'));
    const appsList = el('div', 'workspace-links');
    const navShortcuts = el('div', 'workspace-links');
    for (const [label, target] of [
      ['MCP 与工具权限', 'mcp'],
      ['指令与角色准则', 'instructions'],
      ['安全边界矩阵', 'workspace-access'],
      ['#446 完整清单', 'workspace-inventory'],
    ]) {
      const btn = el('button', 'veil-btn sm', label);
      btn.type = 'button';
      btn.addEventListener('click', () => go(target));
      navShortcuts.append(btn);
    }
    appsCard.append(appsList, navShortcuts);
    rightCol.append(hostCard, appsCard);
    grid.append(wbCard, rightCol);
    host.replaceChildren(grid);

    async function refreshWork() {
      const [wbRes, appRes] = await Promise.all([
        req('/api/workbench').catch(() => null),
        req('/api/windows-apps').catch(() => null),
      ]);
      const wb = wbRes && wbRes.ok ? (await wbRes.json())?.state || {} : { enabled: false, project_roots: [], allowed_hosts: [] };
      const enabled = Boolean(wb.enabled);
      onBtn.setAttribute('aria-pressed', String(enabled));
      offBtn.setAttribute('aria-pressed', String(!enabled));

      folderList.replaceChildren();
      const roots = Array.isArray(wb.project_roots) ? wb.project_roots : [];
      if (!roots.length) {
        folderList.append(el('small', '', '当前尚未授权任何本地项目目录。'));
      } else {
        for (const folder of roots) {
          const row = el('div', 'layer-item-top');
          row.style.padding = '8px 10px';
          row.style.borderRadius = '10px';
          row.style.background = 'var(--surface-muted)';
          row.append(el('span', '', folder));
          const rm = el('button', 'veil-btn sm danger', '移除');
          rm.type = 'button';
          rm.addEventListener('click', async () => {
            await req('/api/workbench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remove: folder }) });
            await refreshWork();
          });
          row.append(rm);
          folderList.append(row);
        }
      }

      hostList.replaceChildren();
      const hosts = Array.isArray(wb.allowed_hosts) ? wb.allowed_hosts : [];
      if (!hosts.length) hostList.append(el('small', '', '暂无网站白名单。'));
      for (const h of hosts) {
        const chip = el('span', 'veil-pill', h);
        const x = el('button', 'veil-btn sm ghost', '×');
        x.type = 'button';
        x.addEventListener('click', async () => {
          await req('/api/workbench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remove_host: h }) });
          await refreshWork();
        });
        chip.append(x);
        hostList.append(chip);
      }

      appsList.replaceChildren();
      const appsData = appRes && appRes.ok ? await appRes.json() : null;
      const apps = appsData?.apps || [];
      appsList.append(el('small', '', apps.length ? `已授权桌面软件 ${apps.length} 个` : '当前未配置额外桌面软件（支持先启动软件再在设置添加）。'));
    }

    onBtn.addEventListener('click', async () => {
      await req('/api/workbench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: true }) });
      await refreshWork();
    });
    offBtn.addEventListener('click', async () => {
      await req('/api/workbench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: false }) });
      await refreshWork();
    });
    addFolderBtn.addEventListener('click', async () => {
      try {
        const folder = await window.veilDesktop?.chooseFolder?.();
        if (folder) {
          await req('/api/workbench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ add: folder }) });
          await refreshWork();
        }
      } catch {
        wbStatus.textContent = '未能选取目录';
      }
    });
    hostForm.addEventListener('submit', async event => {
      event.preventDefault();
      const val = hostInput.value.trim();
      if (!val) return;
      await req('/api/workbench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ add_host: val }) });
      hostInput.value = '';
      await refreshWork();
    });

    await refreshWork();
  }

  async function renderWorkspaceAccess(host, req) {
    const card = el('article', 'surface workspace-card');
    card.append(el('h3', '', '权限与安全约束矩阵（只读）'));
    const status = el('p', '', '正在汇总敏感权限（S）与安全约束（P）清单…');
    const list = el('ul', 'model-list');
    card.append(status, list);
    host.replaceChildren(card);
    try {
      const res = await req('/api/config/inventory');
      if (!res.ok) throw new Error('inventory unavailable');
      const data = await res.json();
      const rows = (data.rows || data.items || []).filter(r => r.category === 'S' || r.category === 'P').slice(0, 40);
      status.textContent = `展示前 ${rows.length} 条敏感权限与安全边界（完整列表见全量清单）。`;
      for (const row of rows) {
        list.append(el('li', '', `[${row.category}] ${row.domain} · ${row.item} — ${row.risk || row.key || ''}`));
      }
    } catch {
      status.textContent = '暂时无法读取权限清单。';
    }
  }

  async function renderCustomPage(page) {
    const entry = ensureCustomSection(page);
    const req = requestFn || ((url, opt) => fetch(url, opt));
    if (window.veilBrain?.isBrainPage?.(page)) {
      await window.veilBrain.renderPage(page, entry.host, req);
      return;
    }
    if (page === 'workspace-home') {
      await renderWorkspaceHome(entry.host);
    } else if (page === 'workspace-work') {
      await renderWorkspaceWork(entry.host, req);
    } else if (page === 'workspace-budget' && typeof window.renderWorkspaceBudget === 'function') {
      await window.renderWorkspaceBudget(entry.host, req);
    } else if (page === 'workspace-inventory' && typeof window.renderWorkspaceInventory === 'function') {
      await window.renderWorkspaceInventory(entry.host, req);
    } else if (page === 'workspace-access') {
      await renderWorkspaceAccess(entry.host, req);
    }
  }

  function showCustomSection(activeCustomPage) {
    for (const [page, { section }] of customSections) {
      section.hidden = page !== activeCustomPage;
    }
    for (const [page, btn] of navExtButtons) {
      btn.setAttribute('aria-current', String(page === activeCustomPage));
    }
  }

  function ensureJumpButtons() {
    if (jumpRoot) return;
    jumpRoot = el('div', 'workspace-jump');
    jumpUp = el('button', '', '↑');
    jumpUp.type = 'button';
    jumpUp.setAttribute('aria-label', '回到顶部');
    jumpDown = el('button', '', '↓');
    jumpDown.type = 'button';
    jumpDown.setAttribute('aria-label', '回到底部');
    jumpRoot.append(jumpUp, jumpDown);
    document.body.append(jumpRoot);

    const activeScroller = () => {
      if (currentPage === 'chat') return document.getElementById('log');
      if (CUSTOM_PAGES.has(currentPage)) return customSections.get(currentPage)?.section || null;
      return [...document.querySelectorAll('.page:not(.workspace-view)')].find(p => !p.hidden) || null;
    };
    const updateJump = () => {
      const box = activeScroller();
      if (!box) {
        jumpRoot.hidden = true;
        return;
      }
      const overflow = box.scrollHeight - box.clientHeight > 120;
      const distBottom = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (currentPage === 'chat') {
        jumpRoot.hidden = distBottom <= 140;
        jumpRoot.classList.add('workspace-chat-jump');
        jumpUp.hidden = true;
        jumpDown.hidden = distBottom <= 140;
        return;
      }
      jumpRoot.hidden = !overflow;
      jumpRoot.classList.remove('workspace-chat-jump');
      jumpUp.hidden = box.scrollTop <= 36;
      jumpDown.hidden = distBottom <= 36;
    };
    jumpUp.addEventListener('click', () => {
      const box = activeScroller();
      if (box) box.scrollTo({ top: 0, behavior: 'smooth' });
    });
    jumpDown.addEventListener('click', () => {
      const box = activeScroller();
      if (box) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
    });
    document.addEventListener('scroll', updateJump, true);
    window.addEventListener('resize', () => {
      updateJump();
      updateSubnav(currentPage);
    });
    window.__veilUpdateJump = updateJump;
  }

  function go(page) {
    if (!page) return;
    currentPage = page;
    if (CUSTOM_PAGES.has(page)) {
      if (typeof nativeSetPageFn === 'function') nativeSetPageFn(page);
      showCustomSection(page);
      updateSubnav(page);
      renderCustomPage(page);
      window.__veilUpdateJump?.();
      return;
    }
    showCustomSection(null);
    if (typeof nativeSetPageFn === 'function') nativeSetPageFn(page);
    updateSubnav(page);
    if (page === 'activity') upgradeActivityLayout();
    window.__veilUpdateJump?.();
  }

  function onPage(page) {
    currentPage = page;
    if (!CUSTOM_PAGES.has(page)) {
      showCustomSection(null);
    } else {
      showCustomSection(page);
      renderCustomPage(page);
    }
    if (page === 'activity') upgradeActivityLayout();
    updateSubnav(page);
    window.__veilUpdateJump?.();
  }

  function init(options = {}) {
    if (initialized) return;
    initialized = true;
    requestFn = options.request || ((url, opt) => fetch(url, opt));
    nativeSetPageFn = options.setPage || null;
    if (!document.body.classList.contains('detached-window')) {
      reorganizeSidebar();
    }
    upgradeActivityLayout();
    initThemePills();
    ensureJumpButtons();

    const detachBtn = document.getElementById('detach-current-btn');
    if (detachBtn) {
      detachBtn.addEventListener('click', () => {
        const target = detachBtn.dataset.detachTarget || 'chat';
        window.veilDesktop?.detachView?.(target).catch(() => {});
      });
    }
    updateSubnav(currentPage);
  }

  window.veilWorkspace = {
    init,
    go,
    onPage,
    isCustom: page => CUSTOM_PAGES.has(page),
    refreshSubnav: () => updateSubnav(currentPage),
    applyVisualTheme: applyVisualThemeState,
    openMemory(memId) {
      if (window.veilBrain?.openMemory) {
        go('memory');
        window.veilBrain.openMemory(memId);
      } else {
        go('memory');
      }
    },
    openEvent(eventId) {
      if (window.veilBrain?.openEvent) {
        go('workspace-events');
        window.veilBrain.openEvent(eventId);
      } else {
        go('workspace-events');
      }
    },
    openRecallTurn(turnId) {
      if (window.veilBrain?.openRecallTurn) {
        go('brain-recall');
        window.veilBrain.openRecallTurn(turnId);
      } else {
        go('brain-recall');
      }
    },
  };

  if (typeof window.workspaceRequest === 'function' && typeof window.veilSetPage === 'function') {
    window.veilWorkspace.init({ request: window.workspaceRequest, setPage: window.veilSetPage });
    window.veilBrain?.mountMemoryLibraryAddon?.(window.workspaceRequest);
  }
})();
