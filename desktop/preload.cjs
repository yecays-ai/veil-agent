'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('companion', {
  toggle: () => ipcRenderer.send('pet:toggle'),
  menu: () => ipcRenderer.send('pet:menu'),
  dragStart: () => ipcRenderer.send('pet:drag-start'),
  dragMove: () => ipcRenderer.send('pet:drag-move'),
  dragEnd: () => ipcRenderer.send('pet:drag-end'),
  resizeStart: corner => ipcRenderer.send('pet:resize-start', corner),
  resizeMove: () => ipcRenderer.send('pet:resize-move'),
  resizeEnd: () => ipcRenderer.send('pet:resize-end'),
  hit: opaque => ipcRenderer.send('pet:hit', opaque === true)
});
