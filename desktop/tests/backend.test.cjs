'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const vm = require('node:vm');
const { readEndpoint, connect } = require('../backend.cjs');

test('a slow screen-pop discovery cannot erase a newer chat connection', async () => {
  const source = await fs.readFile(path.join(__dirname, '..', 'main.cjs'), 'utf8');
  const start = source.indexOf('async function discoverRunningAgentForScreenPop()');
  const end = source.indexOf('\nasync function pollScreenPop()', start);
  assert.ok(start >= 0 && end > start);
  let finish;
  const context = { agentEndpoint: null, quitting: false, app: { isPackaged: false },
    path, __dirname: path.join(__dirname, '..'), agentRoot: () => 'root',
    validRoot: async () => true, readEndpoint: () => new Promise(resolve => { finish = resolve; }) };
  vm.createContext(context);
  const discover = vm.runInContext(`(${source.slice(start, end)})`, context);
  const pending = discover();
  await new Promise(resolve => setImmediate(resolve));
  const connected = { origin: 'new-service' };
  context.agentEndpoint = connected;
  finish(null);
  await pending;
  assert.equal(context.agentEndpoint, connected);
});

test('Windows launchers delegate to the hidden desktop entry', async () => {
  const root = path.join(__dirname, '..', '..');
  const [script, desktopBat, webBat] = await Promise.all([
    fs.readFile(path.join(root, 'start-desktop.vbs'), 'utf8'),
    fs.readFile(path.join(root, 'start-desktop.bat'), 'utf8'),
    fs.readFile(path.join(root, 'start-web.bat'), 'utf8')]);
  assert.match(script, /shell\.Run command, 0, False/);
  assert.match(desktopBat, /wscript\.exe "%~dp0start-desktop\.vbs"/);
  assert.match(webBat, /wscript\.exe "%~dp0start-desktop\.vbs"/);
});

test('the updater migrates the old desktop shortcut to the hidden entry', async () => {
  const root = path.join(__dirname, '..', '..');
  const [update, migration] = await Promise.all([
    fs.readFile(path.join(root, 'update.bat'), 'utf8'),
    fs.readFile(path.join(root, 'scripts', 'update-desktop-shortcut.ps1'), 'utf8')]);
  assert.match(update, /update-desktop-shortcut\.ps1/);
  assert.match(migration, /\$oldTarget = Join-Path \$root 'start-desktop\.bat'/i);
  assert.match(migration, /\$shortcut\.TargetPath = Join-Path \$env:SystemRoot 'System32\\wscript\.exe'/i);
  assert.match(migration, /\$shortcut\.Arguments = '\"' \+ \$script \+ '\"'/i);
});

test('reuses a ready authenticated service without launching or writing Agent data', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'veil-shell-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, 'data'));
  await fs.mkdir(path.join(root, '.venv/Scripts'), { recursive: true });
  for (const file of ['web_launcher.py', 'web_agent.py', '.venv/Scripts/python.exe'])
    await fs.writeFile(path.join(root, file), 'fake fixture');
  const token = 'fictional_token_123456789';
  let ready = true, stopped = false, legacy = false;
  const server = http.createServer((req, res) => {
    assert.equal(req.headers['x-token'], token);
    if (legacy && req.url === '/api/health') { res.writeHead(404); res.end(); return; }
    assert.equal(req.url, legacy ? '/api/events?after=0' : '/api/health');
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ name: 'Fictional', ready, stopped }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const endpointFile = path.join(root, 'data/web_service.json');
  const source = JSON.stringify({ port: server.address().port, token });
  await fs.writeFile(endpointFile, source);
  const history = path.join(root, 'data/fake-chat.jsonl');
  await fs.writeFile(history, 'fictional history bytes\n');
  let launches = 0;
  const launcher = async actualRoot => { assert.equal(actualRoot, root); launches++; ready = true; stopped = false; };
  const endpoint = await connect(root, launcher);
  assert.equal(launches, 0);
  assert.equal(new URL(endpoint.url).hostname, '127.0.0.1');
  legacy = true;
  assert.ok(await readEndpoint(root));
  legacy = false;
  ready = false;
  assert.equal(await readEndpoint(root), null);
  await connect(root, launcher);
  assert.equal(launches, 1);
  stopped = true;
  await connect(root, launcher);
  assert.equal(launches, 2);
  assert.equal(await fs.readFile(endpointFile, 'utf8'), source);
  assert.equal(await fs.readFile(history, 'utf8'), 'fictional history bytes\n');
  await fs.writeFile(endpointFile, JSON.stringify({ port: 123, token: '../unsafe' }));
  assert.equal(await readEndpoint(root), null);
  await fs.writeFile(endpointFile, '{broken');
  assert.equal(await readEndpoint(root), null);
});

test('acceptance data override never falls back to a normal launcher', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'veil-accept-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, '.venv/Scripts'), { recursive: true });
  for (const file of ['web_launcher.py', 'web_agent.py', '.venv/Scripts/python.exe'])
    await fs.writeFile(path.join(root, file), 'fake fixture');
  const previous = process.env.VEIL_AGENT_DATA;
  process.env.VEIL_AGENT_DATA = path.join(root, 'test-data');
  t.after(() => { if (previous === undefined) delete process.env.VEIL_AGENT_DATA; else process.env.VEIL_AGENT_DATA = previous; });
  let launches = 0;
  await assert.rejects(connect(root, () => { launches++; }), /验收实例的后端未运行/);
  assert.equal(launches, 0);
});

test('missing Agent installation fails before invoking a launcher', async () => {
  await assert.rejects(connect('relative', () => { throw new Error('must not launch'); }), /setup.bat/);
});

test('Peter page API is allowed for the main and detached window', async () => {
  const source = await fs.readFile(path.join(__dirname, '..', 'main.cjs'), 'utf8');
  const start = source.indexOf('const MEMORY_POSTS');
  const end = source.indexOf("ipcMain.handle('chat:request'", start);
  assert.ok(start >= 0 && end > start);
  const context = { URL };
  vm.createContext(context);
  const allowed = vm.runInContext(source.slice(start, end) + '; allowedChatRequest', context);
  assert.equal(allowed('/api/peter-page', 'GET'), true);
  assert.equal(allowed('/api/peter-page', 'POST'), true);
  assert.equal(allowed('/api/peter-page?x=1', 'GET'), false);
  assert.equal(allowed('/api/pending-approvals', 'GET'), true);
  assert.equal(allowed('/api/pending-approvals', 'POST'), true);
  assert.equal(allowed('/api/not-peter', 'GET'), false);
});
