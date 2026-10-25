'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('veilDesktop', {
  request: (path, body, timeoutMs) => ipcRenderer.invoke('chat:request', path, body, timeoutMs),
  reconnect: () => ipcRenderer.invoke('chat:reconnect'),
  mediaUrl: filename => ipcRenderer.invoke('chat:media-url', filename),
  attachmentText: id => ipcRenderer.invoke('chat:attachment-text', id),
  openAttachment: id => ipcRenderer.invoke('chat:open-attachment', id),
  petSize: () => ipcRenderer.invoke('chat:pet-size'),
  setPetSize: size => ipcRenderer.invoke('chat:set-pet-size', size),
  saveTheme: theme => ipcRenderer.invoke('chat:save-theme', theme),
  chooseBackground: () => ipcRenderer.invoke('chat:choose-background'),
  getBackground: () => ipcRenderer.invoke('chat:get-background'),
  clearBackground: () => ipcRenderer.invoke('chat:clear-background'),
  collapse: () => ipcRenderer.send('desktop:collapse'),
  expand: () => ipcRenderer.send('desktop:expand'),
  openMemory: () => ipcRenderer.invoke('chat:open-memory'),
  detachView: (view, point) => ipcRenderer.invoke('view:detach', view, point),
  mergeView: view => ipcRenderer.invoke('view:merge', view),
  focusView: view => ipcRenderer.invoke('view:focus', view),
  detachedViews: () => ipcRenderer.invoke('view:states'),
  onDetachedViews: callback => ipcRenderer.on('view:states', (_event, views) => callback(views)),
  onShowActivity: callback => ipcRenderer.on('desktop:show-activity', () => callback()),
  onShowView: callback => ipcRenderer.on('desktop:show-view', (_event, view) => callback(view)),
  onShowSettings: callback => ipcRenderer.on('desktop:show-settings', () => callback())
});
