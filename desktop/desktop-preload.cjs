'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('veilDesktop', {
  request: (path, body, timeoutMs) => ipcRenderer.invoke('chat:request', path, body, timeoutMs),
  reconnect: () => ipcRenderer.invoke('chat:reconnect'),
  mediaUrl: filename => ipcRenderer.invoke('chat:media-url', filename),
  petSize: () => ipcRenderer.invoke('chat:pet-size'),
  setPetSize: size => ipcRenderer.invoke('chat:set-pet-size', size),
  saveTheme: theme => ipcRenderer.invoke('chat:save-theme', theme),
  chooseBackground: () => ipcRenderer.invoke('chat:choose-background'),
  getBackground: () => ipcRenderer.invoke('chat:get-background'),
  clearBackground: () => ipcRenderer.invoke('chat:clear-background'),
  collapse: () => ipcRenderer.send('desktop:collapse'),
  expand: () => ipcRenderer.send('desktop:expand'),
  openMemory: () => ipcRenderer.invoke('chat:open-memory'),
  onShowActivity: callback => ipcRenderer.on('desktop:show-activity', () => callback()),
  onShowSettings: callback => ipcRenderer.on('desktop:show-settings', () => callback())
});
