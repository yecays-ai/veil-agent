"""Offline coverage of the real desktop composer handlers during shell changes."""
from pathlib import Path
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]


class ComposerMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tray = None
        self.buttons = set()

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == 'section' and attributes.get('id') == 'composer-tray':
            self.tray = attributes
        if tag == 'button' and self.tray is not None and not getattr(self, 'after_tray', False):
            self.buttons.add(attributes.get('id'))

    def handle_endtag(self, tag):
        if tag == 'section' and self.tray is not None:
            self.after_tray = True


class DesktopComposerContract(unittest.TestCase):
    def test_plus_contains_existing_actions(self):
        dom = ComposerMarkup()
        dom.feed((ROOT / 'desktop/chat.html').read_text(encoding='utf-8'))
        self.assertIsNotNone(dom.tray)
        self.assertIn('hidden', dom.tray)
        self.assertTrue({'sticker-button', 'transfer-button', 'gift-button',
                         'image-button', 'speech-toggle'} <= dom.buttons)

    @unittest.skipUnless(shutil.which('node'), 'offline renderer test requires Node.js')
    def test_existing_actions_dispatch_in_offline_composer(self):
        # Execute renderer-owned handlers, but stub native UI, microphone, and HTTP.
        # This does not assert CSS layout or invoke a backend.
        js = r"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const script = fs.readFileSync('desktop/chat.js', 'utf8');
        const section = (start, end) => {
          const offset = script.indexOf(start);
          assert.notEqual(offset, -1, start);
          const finish = script.indexOf(end, offset);
          assert.ok(finish > offset, end);
          return script.slice(offset, finish);
        };
        const nodes = new Map();
        const node = id => {
          if (!nodes.has(id)) nodes.set(id, {
            value: '', hidden: true, attributes: {}, events: {},
            classList: {toggle() {}},
            setAttribute(key, value) { this.attributes[key] = value; },
            addEventListener(key, fn) { this.events[key] = fn; },
            showModal() { this.open = true; }, close() { this.open = false; },
            focus() { this.focused = true; }, click() { this.clicked = true; },
            contains(target) { return target === this; },
          });
          return nodes.get(id);
        };
        const posts = [];
        const fakeImage = { name: 'fixture.png' };
        const ctx = vm.createContext({
          $: node, box: node('box'), imageFile: node('image-file'),
          log: node('log'), imageButton: node('image-button'), speechButton: node('speech-toggle'),
          stickerButton: node('sticker-button'), stickerManage: false,
          document: node('document'), ResizeObserver: class { observe() {} },
          crypto: { randomUUID: () => 'fixture-id-123' },
          chooseImage: image => assert.equal(image, fakeImage),
          speechRecorder: null, speechPermissionPending: false,
          startSpeech: () => { ctx.speechRecorder = {}; },
          stopSpeech: cancel => { ctx.speechRecorder = null; ctx.speechPermissionPending = false; ctx.cancel = cancel; },
          openStickerPanel: () => vm.runInContext('setComposerPanel("stickers")', ctx),
          closeStickerPanel: () => vm.runInContext('setComposerPanel(null)', ctx),
          api: async (path, payload) => { posts.push([path, payload]); return {ok: true, json: async () => ({ok: true})}; },
        });
        vm.runInContext([
          section('const composerMore=', "document.querySelectorAll('[data-cmd="),
          section('imageButton.onclick=', "['dragenter','dragover']"),
          section('speechButton.onclick=', "window.addEventListener('pagehide'"),
          section('stickerButton.onclick=', "$('sticker-manage').onclick"),
        ].join('\n'), ctx);
        const tap = id => node(id).onclick({ stopPropagation() {} });
        const tray = node('composer-tray');
        const stickers = node('sticker-panel');
        const modal = node('virtual-card-dialog');
        const plus = node('composer-more');
        (async () => {
          tap('composer-more');
          assert.equal(tray.hidden, false);
          assert.equal(plus.attributes['aria-expanded'], 'true');
          node('box').events.focus();
          assert.equal(tray.hidden, true);
          assert.equal(plus.attributes['aria-expanded'], 'false');
          tap('composer-more'); tap('sticker-button');
          assert.equal(stickers.hidden, false);
          assert.equal(tray.hidden, true);
          node('document').events.click({ target: node('outside') });
          assert.equal(stickers.hidden, true);
          tap('composer-more'); tap('image-button');
          assert.equal(node('image-file').clicked, true);
          node('image-file').files = [fakeImage];
          node('image-file').onchange();
          assert.equal(tray.hidden, true);
          tap('composer-more'); tap('speech-toggle');
          assert.ok(ctx.speechRecorder);
          tap('speech-toggle');
          assert.equal(ctx.cancel, false);
          ctx.speechPermissionPending = true;
          tap('speech-toggle');
          assert.equal(ctx.cancel, true);
          tap('transfer-button');
          assert.ok(modal.open);
          assert.ok(node('virtual-card-amount').required);
          node('virtual-card-amount').value = ' 2.50 ';
          await node('virtual-card-form').onsubmit({ preventDefault() {} });
          assert.equal(modal.open, false);
          tap('composer-more'); tap('gift-button');
          assert.ok(modal.open);
          assert.equal(node('virtual-card-amount').required, false);
          assert.ok(node('virtual-card-content').required);
          node('virtual-card-content').value = 'fixture gift';
          await node('virtual-card-form').onsubmit({ preventDefault() {} });
          assert.equal(modal.open, false);
          assert.deepEqual(posts.map(([path, payload]) => [path, payload.action]), [
            ['/api/message/action', 'create_transfer'], ['/api/message/action', 'create_gift'],
          ]);
          assert.equal(posts[0][1].card.amount, '2.50');
          assert.equal(posts[1][1].card.content, 'fixture gift');
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """
        result = subprocess.run(['node', '-e', js], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
