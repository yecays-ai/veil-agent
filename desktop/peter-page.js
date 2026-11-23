
/* Ticket 394: Peter page. Loads and saves the persona, background, learned context and Lan's persona. */
(function () {
  var $p = function (id) { return document.getElementById(id); };
  var state = null, dirty = new Set(), revisions = {}, queue = Promise.resolve();
  // The four cards on this page; facts, growth and ongoing all live in the "learned" card, so
  // editing any of them has to light up that card (ticket 413: it never did before).
  function cardNode(card) {
    var name = (card === 'ongoing' || card === 'facts' || card === 'growth' || card === 'relationship')
      ? 'learned' : card;
    return document.querySelector('[data-peter-card="' + name + '"]');
  }
  function token(text) {
    if (!text) return 0;
    var ascii = 0;
    for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) < 128) ascii++;
    return Math.ceil(ascii / 4 + (text.length - ascii));
  }
  function count(el, text, limit) {
    if (!el) return;
    text = text || '';
    var bytes = new TextEncoder().encode(text).length;
    el.textContent = text.length + ' 字符 · ' + bytes + ' 字节 · 约 ' + token(text) + ' tokens' + (limit ? ' / ' + limit + ' 字节' : '');
  }
  function shortTime(value) {
    if (!value) return '';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return (date.getMonth() + 1) + '月' + date.getDate() + '日 ' + String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
  }
  function showError(message) {
    var box = $p('peter-learned-error');
    if (!box) return;
    box.hidden = !message;
    box.textContent = message || '';
  }
  function mark(card) {
    dirty.add(card);
    revisions[card] = (revisions[card] || 0) + 1;
    var node = cardNode(card);
    if (node) node.classList.add('peter-dirty');
  }
  function unmark(card) {
    if (dirty.has(card)) return;
    var node = cardNode(card);
    if (node) node.classList.remove('peter-dirty');
  }
  async function request(body) {
    var result = await window.veilDesktop.request('/api/peter-page', body, 30000);
    if (!result.ok) {
      var message = result.data && result.data.error ? result.data.error : '保存失败（' + result.status + '）';
      throw new Error(message);
    }
    return result.data;
  }
  function fillVersions(select, versions) {
    if (!select) return;
    select.replaceChildren(new Option('历史版本', ''));
    var list = versions || [];
    for (let i = 0; i < list.length; i++) select.append(new Option(shortTime(list[i].time), list[i].name));
  }
  function isOpen(item) { return item.status === 'active' || item.status === 'uncertain'; }
  // Ticket 413: only unfinished items are "进行中". Finished ones are kept for Lan and listed in a
  // closed block below; they never reach the model's context and do not use the shared byte budget.
  function renderOngoing(items) {
    var box = $p('peter-ongoing');
    if (!box || !state || !state.learned) return;
    items = items || [];
    state.learned.ongoing.items = items;
    var open = items.filter(isOpen);
    var done = items.filter(function (row) { return row.status === 'completed'; });
    box.replaceChildren();
    open.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'peter-ongoing-item';
      var text = document.createElement('textarea');
      text.value = item.text || '';
      text.placeholder = '现在还没过去的事…';
      text.oninput = function () { item.text = text.value; mark('ongoing'); };
      row.append(text, ongoingMeta(item));
      box.append(row);
    });
    if (!open.length) {
      var empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = '还没有进行中的事。';
      box.append(empty);
    }
    $p('peter-ongoing-count').textContent = open.length + ' 条';
    renderDone(done);
  }
  function ongoingMeta(item) {
    var meta = document.createElement('div');
    meta.className = 'peter-ongoing-meta';
    var status = document.createElement('select');
    [['active', '进行中'], ['uncertain', '尚不确定'], ['completed', '已完成']].forEach(function (value) {
      status.append(new Option(value[1], value[0]));
    });
    status.value = item.status || 'active';
    status.onchange = function () { item.status = status.value; mark('ongoing'); };
    var sources = document.createElement('input');
    sources.value = (item.sources || []).join(', ');
    sources.placeholder = '来源 chat:…, memory:…';
    sources.oninput = function () {
      item.sources = sources.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
      mark('ongoing');
    };
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '删除';
    remove.onclick = function () { removeOngoing(item, remove); };
    meta.append(status, sources, remove);
    return meta;
  }
  // Ticket 422: the stable relationship base. Each row is one thing that is true right now.
  // "不再适用" keeps the row for history but stops it from reaching the model.
  function renderRelationship(items) {
    var box = $p('peter-relationship');
    if (!box || !state || !state.learned) return;
    items = items || [];
    state.learned.relationship = state.learned.relationship || { updated_at: '' };
    state.learned.relationship.items = items;
    var current = items.filter(function (row) { return row.status !== 'outdated'; });
    var old = items.filter(function (row) { return row.status === 'outdated'; });
    box.replaceChildren();
    current.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'peter-ongoing-item';
      var text = document.createElement('textarea');
      text.value = item.text || '';
      text.placeholder = '现在成立的关系共识、边界或习惯…';
      text.oninput = function () { item.text = text.value; mark('relationship'); };
      row.append(text, relationshipMeta(item));
      box.append(row);
    });
    if (!current.length) {
      var empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = '还没有当前有效的记录。';
      box.append(empty);
    }
    if (old.length) {
      var note = document.createElement('p');
      note.className = 'muted';
      note.textContent = '以前成立、现在不再适用 ' + old.length + ' 条（只留作经过，不再带给他）';
      box.append(note);
    }
    $p('peter-relationship-count').textContent = current.length + ' 条';
  }
  function relationshipMeta(item) {
    var meta = document.createElement('div');
    meta.className = 'peter-ongoing-meta';
    var status = document.createElement('select');
    [['confirmed', '已确认'], ['uncertain', '还没确认'], ['outdated', '不再适用']].forEach(function (value) {
      status.append(new Option(value[1], value[0]));
    });
    status.value = item.status || 'confirmed';
    status.onchange = function () { item.status = status.value; mark('relationship'); renderRelationship(state.learned.relationship.items); };
    var source = document.createElement('span');
    source.textContent = ({ lan: '你确认的', evidence: '有来源可查', peter: '他自己的理解（未确认）' })[item.source] || '';
    // Review fix: the smallest evidence a relationship item keeps is visible, not implicit.
    if (item.sources && item.sources.length) source.title = '来源：' + item.sources.join('、');
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '删除';
    remove.onclick = function () { removeRelationship(item, remove); };
    meta.append(status, source, remove);
    return meta;
  }
  async function removeRelationship(item, button) {
    if (!button.dataset.armed) {
      button.dataset.armed = '1';
      button.textContent = '再点一次删除';
      setTimeout(function () {
        if (!button.isConnected) return;
        delete button.dataset.armed;
        button.textContent = '删除';
      }, 4000);
      return;
    }
    delete button.dataset.armed;
    button.disabled = true;
    state.learned.relationship.items = (state.learned.relationship.items || []).filter(function (row) {
      return row.id !== item.id;
    });
    renderRelationship(state.learned.relationship.items);
    renderCounts();
    mark('relationship');
    if (!await save('relationship')) {
      await load();
      showError('这条没有删掉（原因见上）。列表已按硬盘上的内容重新读取。');
    }
  }
  function addRelationship() {
    if (!state || !state.learned) return;
    state.learned.relationship = state.learned.relationship || { updated_at: '' };
    state.learned.relationship.items = state.learned.relationship.items || [];
    state.learned.relationship.items.push({
      id: 'new-' + Date.now().toString(36),
      text: '',
      status: 'confirmed',
      source: 'lan'
    });
    renderRelationship(state.learned.relationship.items);
    mark('relationship');
  }
  function renderDone(done) {
    var details = $p('peter-ongoing-done');
    var list = $p('peter-ongoing-done-list');
    if (!details || !list) return;
    details.hidden = !done.length;
    if (!done.length) {
      $p('peter-ongoing-done-count').textContent = '';
      list.replaceChildren();
      return;
    }
    $p('peter-ongoing-done-count').textContent = done.length + ' 条';
    list.replaceChildren();
    done.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'peter-ongoing-item peter-done-item';
      var text = document.createElement('p');
      text.className = 'peter-done-text';
      text.textContent = item.text || '';
      var meta = document.createElement('div');
      meta.className = 'peter-ongoing-meta';
      var stamp = document.createElement('span');
      stamp.textContent = item.completed_at ? ('完成于 ' + shortTime(item.completed_at)) : '已完成';
      var reopen = document.createElement('button');
      reopen.type = 'button';
      reopen.textContent = '重开';
      reopen.onclick = function () { reopenOngoing(item, reopen); };
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '删除';
      remove.onclick = function () { removeOngoing(item, remove); };
      meta.append(stamp, reopen, remove);
      row.append(text, meta);
      list.append(row);
    });
  }
  async function removeOngoing(item, button) {
    // Two clicks, like the memory page and the life page: no native confirm() to rely on.
    if (!button.dataset.armed) {
      button.dataset.armed = '1';
      button.textContent = '再点一次删除';
      setTimeout(function () {
        if (!button.isConnected) return;
        delete button.dataset.armed;
        button.textContent = '删除';
      }, 4000);
      return;
    }
    delete button.dataset.armed;
    button.disabled = true;
    state.learned.ongoing.items = (state.learned.ongoing.items || []).filter(function (row) {
      return row.id !== item.id;
    });
    renderOngoing(state.learned.ongoing.items);
    renderCounts();
    mark('ongoing');
    if (!await save('ongoing')) {
      await load();
      showError('这条没有删掉（原因见上）。列表已按硬盘上的内容重新读取。');
    }
  }
  async function reopenOngoing(item, button) {
    button.disabled = true;
    item.status = 'active';
    delete item.completed_at;
    renderOngoing(state.learned.ongoing.items);
    renderCounts();
    mark('ongoing');
    if (!await save('ongoing')) await load();
  }
  function renderCounts() {
    var facts = $p('peter-facts'), growth = $p('peter-growth');
    count($p('peter-facts-count'), facts.value, null);
    count($p('peter-growth-count'), growth.value, null);
    var used = new TextEncoder().encode((facts.value || '') + (growth.value || '')).length;
    if (state && state.learned) {
      state.learned.ongoing.items.filter(isOpen).forEach(function (item) {
        used += new TextEncoder().encode(item.text || '').length;
      });
      // Ticket 422: outdated relationship rows are history; only current ones take the budget.
      ((state.learned.relationship && state.learned.relationship.items) || []).forEach(function (item) {
        if (item.status !== 'outdated') used += new TextEncoder().encode(item.text || '').length;
      });
    }
    var max = state && state.learned ? state.learned.max_bytes : 4000;
    $p('peter-learned-total').textContent = used + ' / ' + max + ' 字节';
    var bar = $p('peter-budget-bar');
    if (bar) bar.style.width = Math.min(100, max ? used / max * 100 : 0) + '%';
  }
  function render(data) {
    var pending = new Set(dirty);
    var previous = state;
    state = data;
    var p = data.persona, b = data.background, u = data.user_persona, l = data.learned;
    if (!pending.has('persona')) $p('peter-persona').value = p.text || '';
    count($p('peter-persona-count'), p.text || '', null);
    $p('peter-persona-saved').textContent = p.mtime ? ('保存于 ' + shortTime(p.mtime)) : '每次请求都带';
    fillVersions($p('peter-persona-versions'), p.versions);
    if (!pending.has('background')) $p('peter-background').value = b.text || '';
    count($p('peter-background-count'), b.text || '', null);
    $p('peter-background-saved').textContent = (b.mtime ? ('保存于 ' + shortTime(b.mtime) + ' · ') : '') + '本轮最多带 3 段 / 1,500 字符';
    fillVersions($p('peter-background-versions'), b.versions);
    if (u) {
      if (!pending.has('user')) $p('peter-user').value = u.text || '';
      count($p('peter-user-count'), u.text || '', u.max_bytes);
      $p('peter-user-saved').textContent = (u.mtime ? ('保存于 ' + shortTime(u.mtime) + ' · ') : '') + (u.bytes ? (u.bytes + ' / ' + u.max_bytes + ' 字节') : '');
    }
    if (l && l.available) {
      showError('');
      if (!pending.has('facts')) $p('peter-facts').value = (l.sections.facts && l.sections.facts.text) || '';
      if (!pending.has('growth')) $p('peter-growth').value = (l.sections.growth && l.sections.growth.text) || '';
      $p('peter-facts-saved').textContent = shortTime(l.sections.facts && l.sections.facts.updated_at);
      $p('peter-growth-saved').textContent = shortTime(l.sections.growth && l.sections.growth.updated_at);
      $p('peter-ongoing-saved').textContent = shortTime(l.ongoing && l.ongoing.updated_at);
      // A save that lands while Lan is still typing must not wipe the newer text off the screen.
      if (pending.has('ongoing') && previous && previous.learned && previous.learned.ongoing) {
        l.ongoing.items = previous.learned.ongoing.items;
      } else {
        renderOngoing((l.ongoing && l.ongoing.items) || []);
      }
      $p('peter-relationship-saved').textContent = shortTime(l.relationship && l.relationship.updated_at);
      if (pending.has('relationship') && previous && previous.learned && previous.learned.relationship) {
        l.relationship = l.relationship || {};
        l.relationship.items = previous.learned.relationship.items;
      } else {
        renderRelationship((l.relationship && l.relationship.items) || []);
      }
      renderCounts();
    } else {
      showError((l && l.error) || 'Learned Context 暂不可用。');
    }
    var nodes = document.querySelectorAll('.peter-card');
    for (let i = 0; i < nodes.length; i++) nodes[i].classList.remove('peter-dirty');
    dirty = pending;
    pending.forEach(function (card) {
      var node = cardNode(card);
      if (node) node.classList.add('peter-dirty');
    });
  }
  function enqueue(job) {
    var next = queue.then(job, job);
    queue = next.then(function () {}, function () {});
    return next;
  }
  function load() { return enqueue(doLoad); }
  function save(card, renderPage) { return enqueue(function () { return doSave(card, renderPage); }); }
  async function doLoad() {
    $p('peter-status').textContent = '读取中…';
    try {
      var data = await request({ action: 'load' });
      render(data);
      $p('peter-status').textContent = '已读取';
      return true;
    } catch (error) {
      $p('peter-status').textContent = '';
      showError(error.message);
      return false;
    }
  }
  function payload(card) {
    if (card === 'persona') return { action: 'save_persona', text: $p('peter-persona').value };
    if (card === 'background') return { action: 'save_background', text: $p('peter-background').value };
    if (card === 'user') return { action: 'save_user_persona', text: $p('peter-user').value };
    if (card === 'growth') return { action: 'save_learned', section: 'growth', text: $p('peter-growth').value,
      expected_updated_at: state.learned.sections.growth.updated_at };
    if (card === 'facts') return { action: 'save_learned', section: 'facts', text: $p('peter-facts').value,
      expected_updated_at: state.learned.sections.facts.updated_at };
    if (card === 'ongoing') return { action: 'save_ongoing',
      // A row added with 新增 and left empty would make the whole save fail ("正文为空").
      items: state.learned.ongoing.items.filter(function (item) { return (item.text || '').trim(); }),
      expected_updated_at: state.learned.ongoing.updated_at };
    if (card === 'relationship') return { action: 'save_relationship',
      items: (state.learned.relationship.items || []).filter(function (item) { return (item.text || '').trim(); }),
      expected_updated_at: state.learned.relationship.updated_at };
    return null;
  }
  async function doSave(card, renderPage) {
    var body = payload(card);
    if (!body) return false;
    var revision = revisions[card] || 0;
    $p('peter-status').textContent = '保存中…';
    try {
      var data = await request(body);
      // Only drop the "unsaved" mark if nothing changed while the request was in flight.
      if ((revisions[card] || 0) === revision) {
        dirty.delete(card);
        unmark(card);
      }
      state = data;
      if (renderPage !== false) render(data);
      $p('peter-status').textContent = '已保存 · ' + new Date().toLocaleTimeString();
      return true;
    } catch (error) {
      $p('peter-status').textContent = '';
      showError(error.message);
      return false;
    }
  }
  async function saveAll() {
    var cards = Array.from(dirty);
    for (let i = 0; i < cards.length; i++) {
      if (!await save(cards[i], false)) return false;
    }
    if (state) render(state);
    return true;
  }
  window.peterPageDirty = function () { return dirty.size > 0; };
  window.peterLeaveGuard = function () {
    return new Promise(function (resolve) {
      if (!dirty.size) return resolve('discard');
      var layer = document.createElement('div');
      layer.className = 'chat-confirm-layer';
      var backdrop = document.createElement('button');
      backdrop.type = 'button';
      backdrop.className = 'chat-confirm-backdrop';
      var dialog = document.createElement('section');
      dialog.className = 'chat-confirm-dialog';
      dialog.innerHTML = '<h2>还没保存</h2><p>这次改动要保存吗？</p>';
      var buttons = document.createElement('div');
      buttons.className = 'tool-row';
      var cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = '取消';
      var discard = document.createElement('button'); discard.type = 'button'; discard.textContent = '不保存';
      var saveBtn = document.createElement('button'); saveBtn.type = 'button'; saveBtn.textContent = '保存';
      function close(value) { layer.remove(); resolve(value); }
      cancel.onclick = function () { close('cancel'); };
      discard.onclick = function () { close('discard'); };
      saveBtn.onclick = async function () { saveBtn.disabled = true; var ok = await saveAll(); close(ok ? 'discard' : 'cancel'); };
      buttons.append(cancel, discard, saveBtn);
      dialog.append(buttons);
      layer.append(backdrop, dialog);
      document.body.append(layer);
      saveBtn.focus();
    });
  };
  window.peterLoadPage = load;
  function bind() {
    var pairs = [
      ['persona', 'peter-persona', 'peter-persona-save'],
      ['background', 'peter-background', 'peter-background-save'],
      ['user', 'peter-user', 'peter-user-save'],
      ['growth', 'peter-growth', 'peter-growth-save'],
      ['facts', 'peter-facts', 'peter-facts-save']
    ];
    pairs.forEach(function (pair) {
      var node = $p(pair[1]), action = $p(pair[2]);
      if (node) node.oninput = function () { mark(pair[0]); renderCounts(); };
      if (action) action.onclick = function () { save(pair[0]); };
    });
    $p('peter-ongoing-save').onclick = function () { save('ongoing'); };
    $p('peter-ongoing-add').onclick = function () {
      state.learned.ongoing.items.push({ id: 'new-' + Date.now(), text: '', status: 'active', sources: [] });
      renderOngoing(state.learned.ongoing.items);
      mark('ongoing');
    };
    $p('peter-relationship-save').onclick = function () { save('relationship'); };
    $p('peter-relationship-add').onclick = addRelationship;
    $p('peter-persona-restore').onclick = function () { restore('persona', 'peter-persona-versions'); };
    $p('peter-background-restore').onclick = function () { restore('background', 'peter-background-versions'); };
    window.addEventListener('beforeunload', function (event) {
      if (!dirty.size) return;
      event.preventDefault();
      event.returnValue = '';
    });
  }
  async function restore(section, selectId) {
    var select = $p(selectId);
    var version = select && select.value;
    if (!version) { alert('先选一个历史版本。'); return; }
    if (!confirm('恢复这个历史版本？当前内容会先备份。')) return;
    try {
      var data = await request({ action: 'restore', section: section, version: version });
      render(data);
      $p('peter-status').textContent = '已恢复 · ' + new Date().toLocaleTimeString();
    } catch (error) {
      showError(error.message);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { bind(); if (document.body.dataset.currentPage === 'peter') load(); });
  else { bind(); if (document.body.dataset.currentPage === 'peter') load(); }
})();
