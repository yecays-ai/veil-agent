'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('veilMenu', {
  action: name => ipcRenderer.send('menu:action', name),
  onTheme: callback => ipcRenderer.on('menu:theme', (_event, theme) => callback(theme)),
});
