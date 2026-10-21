'use strict';
// DOM presentation only: this preload exposes no bridge or Node API to the page.
window.addEventListener('DOMContentLoaded', () => {
  const header = document.querySelector('header');
  const memory = document.querySelector('#memory-panel');
  if (header) header.style.flexWrap = 'wrap';
  const heading = header?.querySelector('h1');
  if (heading) heading.style.flexBasis = '100%';
  const speechTools = document.querySelector('.speech-tools');
  if (speechTools) speechTools.style.flexBasis = '100%';
  if (memory) {
    memory.hidden = false; memory.disabled = true;
    memory.textContent = 'Memory / Ombre · Coming Soon';
    memory.title = '桌面入口尚未接通；聊天里的记忆能力继续由原 Agent 提供';
    new MutationObserver(() => { if (memory.hidden) memory.hidden = false; })
      .observe(memory, { attributes: true, attributeFilter: ['hidden'] });
  }
  if (header) {
    const lab = document.createElement('button');
    lab.textContent = 'Lab · Coming Soon'; lab.disabled = true;
    lab.title = '桌面入口尚未接通'; header.append(lab);
  }
  for (const id of ['speech-toggle', 'speech-language']) {
    const control = document.getElementById(id);
    if (control) control.disabled = true;
  }
  const status = document.getElementById('speech-status');
  if (status) status.textContent = '桌面 0.1 暂不支持语音输入，可继续键盘输入。';
});
