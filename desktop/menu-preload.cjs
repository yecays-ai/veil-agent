'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('veilMenu', {
  action: name => ipcRenderer.send('menu:action', name),
  size: value => ipcRenderer.send('menu:action', 'size', value),
});
