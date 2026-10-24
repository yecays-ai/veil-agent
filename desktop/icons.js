'use strict';
(() => {
  const paths = {
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    'arrow-up': '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    brain: '<path d="M12 18V5"/><path d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4"/><path d="M17.6 6.5a3 3 0 1 0-5.6-1.5 3 3 0 1 0-5.6 1.5A3 3 0 0 0 6 12a3 3 0 0 0 1 5.8A3 3 0 0 0 12 19a3 3 0 0 0 5 1.2A3 3 0 0 0 18 14a3 3 0 0 0-.4-7.5Z"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    'circle-check': '<circle cx="12" cy="12" r="10"/><path d="m8 12 2.5 2.5L16 9"/>',
    copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
    'external-link': '<path d="M15 3h6v6M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/>',
    'image-plus': '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/><path d="M19 3v6M16 6h6"/>',
    mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
    'message-square': '<path d="M21 15a4 4 0 0 1-4 4H7l-4 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>',
    'minimize-2': '<path d="m4 14 6 6v-4h4v-4h-4v-4zM20 10l-6-6v4h-4v4h4v4z"/>',
    'maximize-2': '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
    'more-horizontal': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    pencil: '<path d="m18 2 4 4M7.5 20H3v-4.5L16.5 2a2.12 2.12 0 0 1 3 3L6 21"/>',
    pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    play: '<path d="m7 4 13 8-13 8z"/>',
    quote: '<path d="M3 21c3 0 7-1 7-8V5H3v8h4M14 21c3 0 7-1 7-8V5h-7v8h4"/>',
    'rotate-ccw': '<path d="M3 7v6h6"/><path d="M3 13a9 9 0 1 0 3-6.7L3 9"/>',
    select: '<path d="M4 3h6M4 3v6M20 3h-6M20 3v6M4 21h6M4 21v-6M20 21h-6M20 21v-6"/><path d="m8 12 2.5 2.5L16 9"/>',
    star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
    'square-x': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m9 9 6 6m0-6-6 6"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6"/>',
    undo: '<path d="M3 7v6h6"/><path d="M3 13a9 9 0 1 0 2.7-6.4L3 9"/>',
    x: '<path d="m18 6-12 12M6 6l12 12"/>',
  };
  window.veilIcon = (name, size = 20) => `<svg class="lucide-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths['more-horizontal']}</svg>`;
  window.veilIconify = (root = document) => root.querySelectorAll('[data-lucide]').forEach(node => {
    node.innerHTML = window.veilIcon(node.dataset.lucide, Number(node.dataset.size) || 18);
  });
  window.veilIconify();
})();
