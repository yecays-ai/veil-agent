'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { readEndpoint, connect } = require('../backend.cjs');

test('reuses a ready authenticated service without launching or writing Agent data', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'veil-shell-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, 'data'));
  await fs.mkdir(path.join(root, '.venv/Scripts'), { recursive: true });
  for (const file of ['web_launcher.py', 'web_agent.py', '.venv/Scripts/python.exe'])
    await fs.writeFile(path.join(root, file), 'fake fixture');
  const token = 'fictional_token_123456789';
  let ready = true, stopped = false;
  const server = http.createServer((req, res) => {
    assert.equal(req.headers['x-token'], token);
    assert.equal(req.url, '/api/events?after=0');
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

test('missing Agent installation fails before invoking a launcher', async () => {
  await assert.rejects(connect('relative', () => { throw new Error('must not launch'); }), /setup.bat/);
});
