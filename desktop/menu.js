'use strict';
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => window.veilMenu.action(button.dataset.action)));
window.veilMenu.onTheme(theme => { document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light'; });
