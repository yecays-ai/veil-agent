'use strict';
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => window.veilMenu.action(button.dataset.action)));
document.querySelectorAll('[data-size]').forEach(button => button.addEventListener('click', () => window.veilMenu.size(Number(button.dataset.size))));
