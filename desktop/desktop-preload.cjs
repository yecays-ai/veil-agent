'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('veilDesktop', {
  request: (path, body, timeoutMs) => ipcRenderer.invoke('chat:request', path, body, timeoutMs),
  mediaUrl: filename => ipcRenderer.invoke('chat:media-url', filename),
  collapse: () => ipcRenderer.send('desktop:collapse'),
  expand: () => ipcRenderer.send('desktop:expand'),
  openMemory: () => ipcRenderer.invoke('chat:open-memory'),
  onShowActivity: callback => ipcRenderer.on('desktop:show-activity', () => callback())
});
