'use strict';
const button = document.querySelector('#pet');
const img = button.querySelector('img');
const canvas = document.createElement('canvas');
const ctx = canvas.getContext('2d', { willReadFrequently: true });
let alpha, down, moved = false, ignored = false;
img.addEventListener('load', () => {
  canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  ctx.drawImage(img, 0, 0); alpha = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
});
function hit(event) {
  if (!alpha) return true;
  const x = Math.floor(event.clientX / innerWidth * canvas.width);
  const y = Math.floor(event.clientY / innerHeight * canvas.height);
  return x >= 0 && y >= 0 && x < canvas.width && y < canvas.height && alpha[(y * canvas.width + x) * 4 + 3] > 12;
}
button.addEventListener('pointerdown', event => {
  if (event.button !== 0 || !hit(event)) return;
  down = { x: event.screenX, y: event.screenY }; moved = false;
  button.setPointerCapture(event.pointerId); window.companion.dragStart();
});
window.addEventListener('pointermove', event => {
  if (down) {
    if (Math.hypot(event.screenX - down.x, event.screenY - down.y) > 5) moved = true;
    if (moved) window.companion.dragMove();
  } else {
    const next = !hit(event);
    if (next !== ignored) { ignored = next; window.companion.hit(!ignored); }
  }
});
button.addEventListener('pointerup', () => {
  if (!down) return;
  down = null; window.companion.dragEnd();
  if (!moved) window.companion.toggle();
});
button.addEventListener('pointercancel', () => { down = null; window.companion.dragEnd(); });
button.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); window.companion.toggle(); }
});
button.addEventListener('contextmenu', event => { event.preventDefault(); window.companion.menu(); });
