/*
 * VEIL Brain read-only prototype guard.
 *
 * The vendored mnemory UI is a full CRUD console. This prototype intentionally
 * has no write API, so the controls that would only produce a 403 are hidden
 * instead of tempting a click. The server still rejects every mutation on its
 * own; this file is presentation only.
 */
(() => {
  const HIDE = [
    /^\+ Add Memory$/i,
    /^Select$/,
    /^Cancel Select$/,
    /^Edit into successor$/,
    /^Artifacts$/,
    /^Retract$/,
    /^Confirm retraction$/,
    /^Delete$/i,
    /^Bulk Delete$/i,
    /^Consolidate Now$/i,
    /^Apply Selected/i,
    /^Apply All$/i,
    /^Applying\.\.\.$/i,
  ];
  const MARK = 'data-veil-readonly-hidden';
  const hide = (element) => {
    if (!element) return;
    element.style.display = 'none';
    element.setAttribute(MARK, '1');
  };
  const sweep = (root = document) => {
    for (const button of root.querySelectorAll('button')) {
      if (button.hasAttribute(MARK)) continue;
      const text = (button.textContent || '').trim();
      const title = button.getAttribute('title') || '';
      if (HIDE.some((pattern) => pattern.test(text))
          || button.getAttribute('aria-label') === 'Account menu'
          || title === 'Edit memory' || title === 'Delete memory') {
        hide(button);
        continue;
      }
      if (/Privacy erase/i.test(text)) {
        hide(button.closest('.danger-panel') || button);
      }
    }
  };
  const start = () => {
    sweep();
    new MutationObserver(() => sweep()).observe(document.body, { childList: true, subtree: true });
    const banner = document.getElementById('veil-brain-banner');
    if (banner) {
      fetch('/api/stats').then((response) => response.ok ? response.json() : null).then((data) => {
        if (!data?.brain) return;
        banner.textContent = `VEIL Brain 只读原型 · brain.db 共 ${data.brain.memories} 条记忆`
          + `（${data.totals.memories} 条可浏览、${data.brain.tombstone} 条墓碑）`
          + ' · 编辑、删除与修复尚未接入';
      }).catch(() => {});
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
