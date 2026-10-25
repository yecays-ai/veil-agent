'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');

async function validRoot(root) {
  if (typeof root !== 'string' || !path.isAbsolute(root)) return false;
  try {
    await Promise.all(['web_launcher.py', 'web_agent.py', '.venv/Scripts/python.exe']
      .map(file => fs.access(path.join(root, file))));
    return true;
  } catch { return false; }
}

async function readEndpoint(root, { requireReady = true } = {}) {
  try {
    const value = JSON.parse(await fs.readFile(path.join(root, 'data/web_service.json'), 'utf8'));
    if (!Number.isInteger(value.port) || value.port < 1 || value.port > 65535 ||
        typeof value.token !== 'string' || !/^[A-Za-z0-9_-]{16,256}$/.test(value.token)) return null;
    const origin = `http://127.0.0.1:${value.port}`;
    const response = await fetch(`${origin}/api/events?after=0`, {
      headers: { 'X-Token': value.token }, signal: AbortSignal.timeout(1500), redirect: 'error'
    });
    if (!response.ok) return null;
    const state = await response.json();
    if (state.stopped !== false || (requireReady && state.ready !== true)) return null;
    return { url: `${origin}/?t=${encodeURIComponent(value.token)}`, origin, name: state.name };
  } catch { return null; }
}

function launch(root) {
  return new Promise((resolve, reject) => {
    const child = spawn(path.join(root, '.venv/Scripts/python.exe'),
      [path.join(root, 'web_launcher.py'), '--no-browser'], {
        cwd: root, windowsHide: true, stdio: 'ignore', shell: false,
        env: { ...process.env, PYTHONUTF8: '1' }
      });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error('Agent 启动失败，请先确认原网页聊天可正常启动。')));
  });
}

async function connect(root, launcher = launch) {
  if (!await validRoot(root)) throw new Error('请选择已经完成 setup.bat 安装的 VEIL Agent 文件夹。');
  let endpoint = await readEndpoint(root);
  if (!endpoint) {
    await launcher(root);
    endpoint = await readEndpoint(root);
  }
  if (!endpoint) throw new Error('Agent 尚未就绪，请确认原网页聊天可以使用，再点击 Peter 重试。');
  return endpoint;
}

module.exports = { validRoot, readEndpoint, connect };
