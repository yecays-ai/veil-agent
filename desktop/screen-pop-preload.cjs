'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('screenPop', {
  onPayload: callback => ipcRenderer.on('screen-pop:payload', (_event, payload) => callback(payload)),
  onStart: callback => ipcRenderer.on('screen-pop:start', () => callback()),
  ready: () => ipcRenderer.send('screen-pop:ready'),
  dismiss: () => ipcRenderer.send('screen-pop:dismiss'),
  finished: () => ipcRenderer.send('screen-pop:finished')
});
