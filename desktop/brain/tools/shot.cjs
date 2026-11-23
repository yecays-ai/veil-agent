'use strict';
/*
 * Deterministic screenshot helper for the VEIL Brain prototype.
 *
 *   electron desktop/brain/tools/shot.cjs \
 *     --url "http://127.0.0.1:8799/ui/?tab=memories&t=..." \
 *     --out <repo>/.local-tmp/p4-artifacts/memories.png [--eval "document.querySelector('.memory-card').click()"]
 *
 * Runs in Electron (the desktop app's own engine), not in an external browser,
 * so layout, fonts and CSP match what the app will show.
 */
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

app.disableHardwareAcceleration();

function arg(name, fallback = '') {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const url = arg('url');
const out = path.resolve(arg('out', path.join(__dirname, '..', '..', '..', '.local-tmp', 'p4-artifacts', 'shot.png')));
const evaluate = arg('eval');
const waitMs = Number(arg('wait', '2500'));

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  if (!url) { console.error('missing --url'); app.exit(2); return; }
  const win = new BrowserWindow({
    width: 1440,
    height: 1000,
    show: false,
    webPreferences: { offscreen: false, contextIsolation: true, nodeIntegration: false },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  await win.loadURL(url);
  await sleep(waitMs);
  const denied = await win.webContents.executeJavaScript(
    "document.body && /token required|forbidden host/i.test(document.body.innerText) && document.body.innerText.length < 200", true);
  if (denied) { console.error('server rejected the token or host'); app.exit(3); return; }
  if (evaluate) {
    const result = await win.webContents.executeJavaScript(evaluate, true);
    console.log('eval completed:', result === undefined ? 'no value' : typeof result);
    await sleep(1200);
  }
  const image = await win.webContents.capturePage();
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, image.toPNG());
  console.log('wrote', out, image.getSize());
  app.exit(0);
}).catch((error) => { console.error(error); app.exit(1); });
