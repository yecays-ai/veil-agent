'use strict';
const popup = document.querySelector('#popup');
const danmaku = document.querySelector('#danmaku');
const confirm = document.querySelector('#confirm');
const close = document.querySelector('#close');
let type = '';
let finished = false;

window.screenPop.onPayload(payload => {
  if (!payload || typeof payload.id !== 'string' || typeof payload.text !== 'string') return;
  type = payload.type;
  if (type === 'popup') {
    document.title = payload.name || 'Peter';
    document.querySelector('#name').textContent = payload.name || 'Peter';
    document.querySelector('#text').textContent = payload.text;
    popup.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => window.screenPop.ready()));
  } else if (type === 'danmaku' && /^#[0-9a-f]{6}$/i.test(payload.color || '') &&
      ['scroll', 'fade', 'bounce'].includes(payload.animation) &&
      ['top', 'center', 'bottom'].includes(payload.position) &&
      Number.isInteger(payload.duration) && payload.duration >= 1 && payload.duration <= 20) {
    const text = document.querySelector('#danmaku-text');
    text.textContent = payload.text;
    text.style.color = payload.color;
    text.style.setProperty('--position-y', { top: '12%', center: '50%', bottom: '88%' }[payload.position]);
    text.style.setProperty('--duration', `${payload.duration}s`);
    danmaku.dataset.animation = payload.animation;
    danmaku.dataset.position = payload.position;
    danmaku.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => window.screenPop.ready()));
  }
});

window.screenPop.onStart(() => {
  if (type === 'popup') { confirm.disabled = false; close.disabled = false; }
  if (type === 'danmaku') {
    danmaku.classList.add('running');
    document.querySelector('#danmaku-text').addEventListener('animationend', () => {
      if (finished) return;
      finished = true;
      window.screenPop.finished();
    }, { once: true });
  }
});

confirm.addEventListener('click', () => window.screenPop.dismiss());
close.addEventListener('click', () => window.screenPop.dismiss());
