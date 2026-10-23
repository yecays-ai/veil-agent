'use strict';
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');

const origin = process.env.VEIL_RUNTIME_ORIGIN;
const token = process.env.VEIL_RUNTIME_TOKEN;
const userData = process.env.VEIL_RUNTIME_USER_DATA;
const failSafe = setTimeout(() => app.exit(1), 30000);
const wait = async (predicate, message) => {
  const end = Date.now() + 15000;
  while (Date.now() < end) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 80));
  }
  const detail = typeof message === 'function' ? await message() : message;
  throw new Error(detail);
};

(async () => {
  assert.ok(origin && token && userData, 'runtime fixture environment is incomplete');
  app.setPath('userData', userData);
  const backend = require('../backend.cjs');
  backend.validRoot = async () => true;
  backend.connect = async () => ({ url: origin + '/?t=' + encodeURIComponent(token), origin });

  await app.whenReady();
  require('../main.cjs');
  await app.whenReady();

  let pet;
  await wait(() => {
    pet = BrowserWindow.getAllWindows().find(window =>
      window.webContents.getURL().endsWith('pet.html'));
    return pet && pet.isVisible();
  }, 'fictional pet window did not open');

  await pet.webContents.executeJavaScript(
    'document.querySelector("#pet").dispatchEvent(new KeyboardEvent("keydown", {key:"Enter",bubbles:true}))');
  let chat;
  await wait(() => {
    chat = BrowserWindow.getAllWindows().find(window => window !== pet);
    return chat && chat.isVisible();
  }, 'real Web Agent chat window did not open');
  await wait(() => chat.webContents.executeJavaScript(
    '!!document.querySelector("#send") && !document.querySelector("#send").disabled'),
    'chat page did not become ready');

  await chat.webContents.executeJavaScript(
    'document.querySelector("#box").value="temporary provider failure";document.querySelector("#send").click()');
  await wait(() => chat.webContents.executeJavaScript(
    'Array.from(document.querySelectorAll(".error")).some(node => node.textContent.includes("未自动重试"))'),
    'provider failure was not shown as a single non-retried error');

  let state = await fetch(origin + '/api/events?after=0', {
    headers: { 'X-Token': token }
  }).then(response => response.json());
  assert.equal(state.ready, true);
  assert.equal(state.stopped, false);
  assert.equal(state.events.filter(event => event.kind === 'error').length, 1);

  await wait(() => chat.webContents.executeJavaScript(
    '!!document.querySelector("#send") && !document.querySelector("#send").disabled'),
    'chat did not recover after the provider failure');
  await chat.webContents.executeJavaScript(
    'document.querySelector("#box").value="continue after recovery";document.querySelector("#send").click()');
  await wait(() => chat.webContents.executeJavaScript(
    'Array.from(document.querySelectorAll(".msg.assistant")).some(node => node.textContent.includes("Fictional recovery reply."))'),
    async () => 'recovered reply did not reach Electron; DOM=' + await chat.webContents.executeJavaScript('document.querySelector(\"#col\" ).innerText') + '; events=' + JSON.stringify(await fetch(origin + '/api/events?after=0', {headers: {'X-Token': token}}).then(response => response.json())));

  state = await fetch(origin + '/api/events?after=0', {
    headers: { 'X-Token': token }
  }).then(response => response.json());
  assert.equal(state.ready, true);
  assert.equal(state.stopped, false);
  console.log('RUNTIME CLOSEOUT PASS: Electron used the live Python Web Agent; one fictional provider failure was not retried and the next request succeeded.');
  clearTimeout(failSafe);
  app.exit(0);
})().catch(error => {
  console.error(error);
  clearTimeout(failSafe);
  app.exit(1);
});
