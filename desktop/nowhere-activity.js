'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.VeilNowhereActivity = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  function toolKind(name) {
    const match = /^mcp(?:__|_)nowhere(?:__|_)(say|quotes|bury)$/i.exec(String(name || ''));
    return match ? match[1].toLowerCase() : null;
  }

  function parsedResult(detail) {
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) return detail;
    if (typeof detail !== 'string' || !detail.trim()) return null;
    try {
      const value = JSON.parse(detail);
      return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
    } catch (_) {
      return null;
    }
  }

  function sameActivity(left, right) {
    if (left.autonomy_run_id && right.autonomy_run_id)
      return left.autonomy_run_id === right.autonomy_run_id;
    return Boolean(left.request_id && right.request_id && left.request_id === right.request_id);
  }

  function sayCounts(item, peers) {
    const texts = new Set();
    for (const peer of peers || []) {
      if (peer === item || peer.state !== '成功' || toolKind(peer.canonical_tool || peer.tool) !== 'say' ||
          peer.result_state === '已截断' || peer.result_state === '结果过长' ||
          peer.tool_input_truncated === true ||
          typeof peer.tool_input !== 'string' || !sameActivity(item, peer)) continue;
      texts.add(peer.tool_input);
    }
    return texts;
  }

  function parse(item, peers = []) {
    const kind = toolKind(item && (item.canonical_tool || item.tool));
    if (!kind || item.state !== '成功' || item.result_state === '已截断' ||
        item.result_state === '结果过长') return null;
    const result = parsedResult(item.detail);
    const data = result && result.data;
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;

    if (kind === 'say') {
      if (data.saved !== true) return null;
      return { kind, text: typeof item.tool_input === 'string' ? item.tool_input : null,
        truncated: item.tool_input_truncated === true };
    }

    if (kind === 'quotes') {
      if (!Array.isArray(data.quotes)) return null;
      const sayTexts = sayCounts(item, peers);
      let duplicateCount = 0;
      const quotes = [];
      for (const quote of data.quotes) {
        if (!quote || typeof quote !== 'object' || Array.isArray(quote) ||
            typeof quote.text !== 'string') return null;
        if (sayTexts.has(quote.text)) {
          duplicateCount += 1;
          continue;
        }
        quotes.push({
          text: quote.text,
          place: typeof quote.place === 'string' ? quote.place : '',
          sim_time: typeof quote.sim_time === 'string' ? quote.sim_time : '',
          pos: Array.isArray(quote.pos) ? quote.pos : null,
        });
      }
      return { kind, quotes, duplicateCount, returnedCount: data.quotes.length };
    }

    const buried = data.buried;
    if (!buried || typeof buried !== 'object' || Array.isArray(buried)) return null;
    return { kind,
      name: typeof buried.name === 'string' ? buried.name : '',
      description: typeof buried.desc === 'string' ? buried.desc : '',
      source: typeof buried.from === 'string' ? buried.from : '',
      pos: Array.isArray(buried.pos) ? buried.pos : null,
      buried_at: typeof buried.buried_at === 'string' ? buried.buried_at : '',
      note: typeof buried.note === 'string' ? buried.note : '' };
  }

  return { toolKind, parse };
});
