/* Ticket 432: developer diagnostics page. Read-only; never writes Peter's data.
   Loaded after chat.js, so api()/notify()/setPage()/openSnapshot() are available. */
(function () {
  var overview = null, links = [], loading = false, traceBox = null;

  function $(id) { return document.getElementById(id); }

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function clear(host) { while (host && host.firstChild) host.removeChild(host.firstChild); }

  // Ticket 432 review: a failed read must never look like "nothing is wrong".
  function fetchJson(path) {
    return api(path).then(function (response) {
      if (!response || response.ok !== true) {
        var status = response && response.status ? response.status : 0;
        var detail = response && response.json
          ? response.json().then(function (body) { return body && body.error; },
            function () { return ''; })
          : Promise.resolve('');
        return detail.then(function (message) {
          throw new Error(message || ('HTTP ' + status));
        });
      }
      return response.json();
    });
  }

  function renderFailure(message) {
    overview = null;
    links = [];
    ['diag-system', 'diag-modules', 'diag-config', 'diag-issues', 'diag-links'].forEach(function (id) {
      var host = $(id);
      if (!host) return;
      clear(host);
      host.appendChild(node('p', 'note-status expired', '诊断读取失败：' + message));
    });
    ['diag-modules-summary', 'diag-config-summary', 'diag-issues-summary'].forEach(function (id) {
      var summary = $(id);
      if (summary) summary.textContent = '读取失败';
    });
    var version = $('diag-version');
    if (version) version.textContent = '';
    var detail = $('diag-detail');
    if (detail) { detail.hidden = true; clear(detail); }
    var state = $('diag-state');
    if (state) state.textContent = '诊断读取失败';
  }

  function shortTime(value) {
    if (!value) return '';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    var now = new Date();
    var sameDay = date.toDateString() === now.toDateString();
    var clock = String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
    return sameDay ? clock : (date.getMonth() + 1) + '月' + date.getDate() + '日 ' + clock;
  }

  function kb(bytes) {
    var number = Number(bytes || 0);
    return number >= 1024 ? (number / 1024).toFixed(1) + ' KB' : Math.round(number) + ' B';
  }

  function copy(text, done) {
    var work = window.veilDesktop && window.veilDesktop.copyText
      ? window.veilDesktop.copyText(text)
      : navigator.clipboard.writeText(text);
    Promise.resolve(work).then(function () { notify(done || '已复制。'); },
      function () { notify('复制失败，请重试。', 'error'); });
  }

  function stateClass(state) {
    if (state === 'running') return 'note-status done';
    if (state === 'error') return 'note-status expired';
    if (state === 'attention' || state === 'paused') return 'note-status active';
    return 'note-status';
  }

  function row() {
    var box = node('div', 'context-row');
    var head = node('div', 'head');
    box.appendChild(head);
    return { box: box, head: head };
  }

  function meta(box, text) {
    if (!text) return;
    box.appendChild(node('div', 'meta', text));
  }

  function renderService(data) {
    var host = $('diag-system');
    if (!host) return;
    clear(host);
    var service = data.service || {};
    var memory = service.memory || {};
    var memoryText = memory.percent !== undefined
      ? memory.percent + '%（可用 ' + kb(memory.free) + ' / 共 ' + kb(memory.total) + '）'
      : (memory.load1 !== undefined ? 'load ' + memory.load1 : '未知');
    var rows = [
      ['版本', (service.commit || '未知') + '（' + (service.branch || '?') + '）· 远端 ' + (service.remote_commit || '未知')],
      ['服务', (service.stopped ? '已停止' : service.ready ? '运行中' : '启动中')
        + (service.uptime_seconds ? ' · 已运行 ' + Math.floor(service.uptime_seconds / 60) + ' 分' : '')
        + (service.started_at ? ' · 上次启动 ' + shortTime(service.started_at) : '')],
      ['系统', (service.platform || '?') + ' · ' + (service.cpu_count || '?') + ' 核 · 内存 ' + memoryText],
      ['数据', kb(service.data_bytes) + ' · 模型 ' + (service.model || '未配置') + ' · Python ' + (service.python || '?')],
      ['崩溃', service.error_file || '无记录']
    ];
    rows.forEach(function (pair) {
      var item = row();
      item.head.appendChild(node('strong', '', pair[0]));
      item.head.appendChild(node('span', 'meta', pair[1]));
      host.appendChild(item.box);
    });
    var version = $('diag-version');
    if (version) version.textContent = shortTime(data.generated_at);
  }

  function renderModules(data) {
    var host = $('diag-modules');
    if (!host) return;
    clear(host);
    var modules = data.modules || [];
    var summary = $('diag-modules-summary');
    if (summary) {
      var attention = modules.filter(function (m) {
        return m.state === 'attention' || m.state === 'error';
      }).length;
      summary.textContent = attention ? attention + ' 个需要看' : '都正常';
    }
    modules.forEach(function (module) {
      var item = row();
      item.head.appendChild(node('strong', '', module.name));
      item.head.appendChild(node('span', stateClass(module.state), module.state_label));
      if (module.last_ok_at) item.head.appendChild(node('time', '', '正常 ' + shortTime(module.last_ok_at)));
      if (module.last_fail_at) item.head.appendChild(node('time', '', '失败 ' + shortTime(module.last_fail_at)));
      if (module.reason) meta(item.box, module.reason);
      var evidence = node('div', 'meta', '依据：' + (module.evidence || ''));
      evidence.title = module.evidence || '';
      item.box.appendChild(evidence);
      host.appendChild(item.box);
    });
  }

  function renderConfig(data) {
    var host = $('diag-config');
    if (!host) return;
    clear(host);
    var items = data.config || [];
    var summary = $('diag-config-summary');
    if (summary) summary.textContent = items.length ? items.length + ' 条' :
      (data.generated_at ? '没有发现问题' : '还没算过');
    if (!items.length) {
      host.appendChild(node('p', 'muted', '没有发现配置问题（工具预算需要发生过一次真实请求才能计算）。'));
      return;
    }
    items.forEach(function (item) {
      var box = row();
      var chip = item.level === 'error' ? 'expired' : item.level === 'warning' ? 'active' : '';
      box.head.appendChild(node('span', 'note-status ' + chip, item.level === 'unknown' ? '还没算' :
        item.level === 'error' ? '要处理' : item.level === 'warning' ? '超出/警告' : '提醒'));
      box.head.appendChild(node('strong', '', item.title || ''));
      var copyButton = node('button', '', '复制');
      copyButton.type = 'button';
      copyButton.onclick = function () {
        copy([item.title, item.detail, '证据：' + (item.evidence || ''), item.hint || ''].join('\n'),
          '配置问题已复制。');
      };
      box.head.appendChild(copyButton);
      meta(box.box, item.detail || '');
      meta(box.box, item.hint || '');
      host.appendChild(box.box);
    });
  }

  function issueBadges(issue) {
    var parts = [];
    if (issue.confirmed === false) parts.push('可能关联，未经确认');
    if (issue.recovery === 'confirmed') parts.push('已恢复');
    else if (issue.confirmed !== false) parts.push('恢复未知');
    if (issue.denial) parts.push(issue.denial.label + (issue.denial.confidence === 'exact' ? '' : '（按文案判断）'));
    return parts.join(' · ');
  }

  function renderIssues(data) {
    var host = $('diag-issues');
    if (!host) return;
    clear(host);
    var items = (data.issues || []).filter(function (item) {
      return item.severity !== 'info' || item.kind === 'denial';
    }).slice(0, 30);
    var summary = $('diag-issues-summary');
    if (summary) summary.textContent = (data.counts && data.counts.issues ? data.counts.issues : 0) + ' 条记录';
    if (!items.length) {
      host.appendChild(node('p', 'muted', '最近 ' + (data.hours || 72) + ' 小时没有异常。'));
      return;
    }
    items.forEach(function (issue) {
      var box = row();
      box.head.appendChild(node('time', '', shortTime(issue.time)));
      box.head.appendChild(node('span', 'note-status ' + (issue.severity === 'error' ? 'expired' :
        issue.severity === 'attention' ? 'active' : ''), issue.module_label || ''));
      box.head.appendChild(node('strong', '', issue.title || ''));
      var copyButton = node('button', '', '复制');
      copyButton.type = 'button';
      copyButton.onclick = function () {
        fetchJson('/api/diagnostics/report?scope=issue&id=' + encodeURIComponent(issue.id))
          .then(function (body) { copy(body.text || '', '这条异常已复制。'); })
          .catch(function () { notify('这条暂时复制不了。', 'error'); });
      };
      box.head.appendChild(copyButton);
      var traceButton = node('button', '', '这一轮');
      traceButton.type = 'button';
      traceButton.onclick = function () { loadTrace(issue); };
      box.head.appendChild(traceButton);
      var badges = issueBadges(issue);
      if (badges) meta(box.box, badges);
      if (issue.detail) meta(box.box, String(issue.detail).slice(0, 240));
      host.appendChild(box.box);
    });
  }

  function renderLinks() {
    var host = $('diag-links');
    if (!host) return;
    clear(host);
    links.forEach(function (item) {
      var box = row();
      box.head.appendChild(node('strong', '', item.name));
      if (item.state === 'ready' && item.url) {
        var open = node('button', '', '打开');
        open.type = 'button';
        open.onclick = function () {
          if (window.veilDesktop && window.veilDesktop.openMaintenance) {
            window.veilDesktop.openMaintenance(item.id).then(function (ok) {
              if (!ok) notify('打不开这个地址。', 'error');
            });
          } else {
            notify('这个版本还不支持打开外部维护页。', 'error');
          }
        };
        box.head.appendChild(open);
      } else if (item.state === 'page') {
        var page = node('button', '', '去这一页');
        page.type = 'button';
        page.onclick = function () { setPage(item.page); };
        box.head.appendChild(page);
      }
      meta(box.box, item.hint || '');
      host.appendChild(box.box);
    });
  }

  function traceLine(chain) {
    var box = $('diag-detail');
    if (!box) return;
    box.hidden = false;
    clear(box);
    var head = node('h3', '', '这一轮发生了什么');
    box.appendChild(head);
    if (chain.confirmed === false) {
      box.appendChild(node('p', 'note-status active',
        '可能关联，未经确认：这条记录没有关联 id，不能断定属于哪一轮。'));
    }
    var reply = node('div', 'muted', chain.request_id ? ('request ' + chain.request_id) : '');
    if (chain.request_ids && chain.request_ids.length > 1)
      reply.textContent += '（这次唤醒共 ' + chain.request_ids.length + ' 个请求）';
    if (chain.run_id) reply.textContent += (reply.textContent ? ' · ' : '') + 'run ' + chain.run_id;
    box.appendChild(reply);

    var steps = [];
    if (chain.messages && chain.messages.length) {
      steps.push('你这条消息：' + chain.messages.filter(function (m) { return m.role === 'user'; }).length +
        ' 条（只显示字数，正文不进诊断页）');
    } else if (chain.note) {
      steps.push(chain.note);
    } else if (chain.run_id) {
      steps.push('这次唤醒没有单独的聊天消息（只记了它自己发起的请求）。');
    }
    if (chain.autonomy) {
      steps.push('自主活动：' + (chain.autonomy.result || '?') + ' · ' +
        (chain.autonomy.result_reason || '').slice(0, 160));
    }
    if (chain.recall) {
      var recall = chain.recall;
      steps.push('召回：Ombre ' + (recall.ombre || []).length + ' 条 · 不可见 ' +
        (recall.hidden || []).length + ' · 判官选 ' + ((recall.judge || {}).picked || []).length +
        ' · 注入 ' + (recall.injected || []).length +
        ((recall.budget_dropped || []).length ? ' · 预算裁掉 ' + recall.budget_dropped.length : '') +
        (recall.failure ? ' · 失败于 ' + recall.failure : ''));
    }
    (chain.model_calls || []).forEach(function (call, index) {
      steps.push('模型请求 ' + (call.step || index + 1) + '：' +
        (call.sent === false ? '未发送（预检）' : (call.usage_status || '已发送')) +
        (call.latency_seconds ? ' · ' + call.latency_seconds + ' 秒' : '') +
        (call.finish_reason ? ' · ' + call.finish_reason : ''));
    });
    (chain.tools || []).forEach(function (tool) {
      steps.push('工具 ' + (tool.tool || '?') + '：' + (tool.state || '?') +
        (tool.call_id ? ' · ' + tool.call_id : ''));
    });
    var list = node('div', 'context-list');
    steps.forEach(function (text) { list.appendChild(node('div', 'meta', text)); });
    box.appendChild(list);

    var actions = node('div', 'diag-actions');
    if (chain.request_id && typeof openSnapshot === 'function') {
      var snapshot = node('button', '', '打开这一轮的请求快照');
      snapshot.type = 'button';
      snapshot.onclick = function () { openSnapshot(chain.request_id, ''); };
      actions.appendChild(snapshot);
    }
    var close = node('button', '', '收起');
    close.type = 'button';
    close.onclick = function () { box.hidden = true; };
    actions.appendChild(close);
    box.appendChild(actions);
  }

  function loadTrace(issue) {
    var query = issue.request_id ? 'request_id=' + encodeURIComponent(issue.request_id)
      : issue.run_id ? 'run_id=' + encodeURIComponent(issue.run_id)
        : issue.call_id ? 'call_id=' + encodeURIComponent(issue.call_id) : '';
    if (!query) {
      notify('这条记录没有可跳转的关联 id。', 'error');
      return;
    }
    fetchJson('/api/diagnostics/trace?' + query).then(function (body) {
      if (!body.chain) {
        notify('查不到这一轮。', 'error');
        return;
      }
      traceLine(body.chain);
    }).catch(function (error) {
      notify('这一轮暂时读不到（' + (error && error.message ? error.message : '连接问题') + '）。', 'error');
    });
  }

  function load() {
    if (loading) return;
    loading = true;
    var state = $('diag-state');
    if (state) state.textContent = '读取中…';
    Promise.all([
      fetchJson('/api/diagnostics/overview'),
      fetchJson('/api/diagnostics/links')
    ]).then(function (both) {
      overview = both[0] || {};
      links = (both[1] || {}).items || [];
      if (!overview.generated_at) throw new Error('返回里没有 overview 数据');
      renderService(overview);
      renderModules(overview);
      renderConfig(overview);
      renderIssues(overview);
      renderLinks();
      if (state) state.textContent = '更新于 ' + shortTime(overview.generated_at);
    }).catch(function (error) {
      renderFailure(error && error.message ? error.message : '连接问题');
      notify('诊断读取失败；聊天和其他页面不受影响。', 'error');
    }).then(function () { loading = false; });
  }

  function copyReport() {
    var button = $('diag-copy');
    if (button) button.disabled = true;
    fetchJson('/api/diagnostics/report?scope=overview&hours=72')
      .then(function (body) {
        if (!body.text) throw new Error('empty');
        copy(body.text, '诊断报告已复制（不含聊天和记忆正文）。');
      })
      .catch(function () { notify('报告暂时生成不了。', 'error'); })
      .then(function () { if (button) button.disabled = false; });
  }

  window.diagnosticsLoad = load;
  if ($('diag-refresh')) $('diag-refresh').onclick = load;
  if ($('diag-copy')) $('diag-copy').onclick = copyReport;

  document.addEventListener('DOMContentLoaded', function () {
    if (document.body && document.body.dataset.currentPage === 'diagnostics') load();
  });
  setTimeout(function () {
    if (document.body && document.body.dataset.currentPage === 'diagnostics') load();
  }, 300);
})();
