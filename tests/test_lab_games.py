import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from lab_games import LabGames
from web_agent import start_server


class LabGameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.lab = Path(self.temp.name) / 'lab'
        self.game = self.lab / 'spider'
        self.game.mkdir(parents=True)
        (self.game / 'index.html').write_text('<html><head></head><body>Play</body></html>', encoding='utf-8')
        self.service = LabGames(self.lab, 9000)
        self.url = self.service.open('lab/spider/index.html')
        self.capability = urlsplit(self.url).path.split('/')[2]

    def score(self, round_id='round_0001', **changes):
        row = {'game_id': 'spider/index.html', 'round_id': round_id, 'score': 10,
               'duration_ms': 5000, 'ending': 'Victory', 'ended_at': '2026-10-30T12:00:00Z'}
        row.update(changes)
        return json.dumps(row).encode()

    def test_playable_page_and_two_durable_rounds(self):
        body, mime = self.service.resource(self.capability, 'index.html')
        self.assertIn(b'veil-score.js', body)
        self.assertEqual(mime, 'text/html')
        self.assertEqual(self.service.save(self.capability, self.score()), {'ok': True, 'duplicate': False})
        self.assertEqual(self.service.save(self.capability, self.score()), {'ok': True, 'duplicate': True})
        self.assertEqual(self.service.save(self.capability, self.score('round_0002', score=20)),
                         {'ok': True, 'duplicate': False})
        rows = [json.loads(line) for line in (self.game / 'scores.jsonl').read_text().splitlines()]
        self.assertEqual([row['score'] for row in rows], [10, 20])
        restarted = LabGames(self.lab, 9001)
        new_capability = urlsplit(restarted.open('lab/spider/index.html')).path.split('/')[2]
        self.assertEqual(restarted.save(new_capability, self.score()), {'ok': True, 'duplicate': True})

    def test_nested_static_resources_stay_in_game_folder(self):
        scripts = self.game / 'js'
        scripts.mkdir()
        (scripts / 'game.js').write_text('window.started = true', encoding='utf-8')
        body, mime = self.service.resource(self.capability, 'js/game.js')
        self.assertIn(b'window.started', body)
        self.assertIn('javascript', mime)
        with self.assertRaises(ValueError):
            self.service.resource(self.capability, '../notes.txt')

    def test_invalid_scores_and_links_do_not_modify_other_files(self):
        other = self.game / 'notes.txt'
        other.write_text('keep', encoding='utf-8')
        for body in (self.score(game_id='../notes.txt'), self.score(score=True),
                     self.score(ending='x' * 200), self.score(round_id='../escape')):
            with self.assertRaises(ValueError):
                self.service.save(self.capability, body)
        self.assertEqual(other.read_text(), 'keep')
        self.assertFalse((self.game / 'scores.jsonl').exists())
        with self.assertRaises(ValueError):
            self.service.resource(self.capability, '../notes.txt')
        target = Path(self.temp.name) / 'outside.jsonl'
        target.write_text('private')
        try:
            (self.game / 'scores.jsonl').symlink_to(target)
        except OSError:
            return
        with self.assertRaises(OSError):
            self.service.save(self.capability, self.score())
        self.assertEqual(target.read_text(), 'private')

    def test_http_entry_score_receipt_and_cross_origin_rejection(self):
        hub = SimpleNamespace(game_manager=None)
        server = start_server(hub, 'private-chat-token', 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        hub.game_manager = LabGames(self.lab, server.server_address[1])
        url = hub.game_manager.open('lab/spider/index.html')
        try:
            with urlopen(url) as response:
                self.assertIn(b'veil-score.js', response.read())
        except URLError as exc:
            if isinstance(exc.reason, PermissionError):
                self.skipTest('Loopback connections are blocked in this environment')
            raise
        (self.game / 'js').mkdir()
        (self.game / 'js' / 'game.js').write_text('window.playable = true', encoding='utf-8')
        with urlopen(url.rsplit('/', 1)[0] + '/js/game.js') as response:
            self.assertIn(b'window.playable', response.read())
        score_url = url.rsplit('/', 1)[0] + '/score'
        origin = f'http://127.0.0.1:{server.server_address[1]}'
        request = Request(score_url, self.score(), {'Content-Type': 'application/json', 'Origin': origin},
                          method='POST')
        with urlopen(request) as response:
            self.assertEqual(json.load(response)['duplicate'], False)
        with urlopen(request) as response:
            self.assertEqual(json.load(response)['duplicate'], True)
        wrong = Request(score_url, self.score('round_0002'),
                        {'Content-Type': 'application/json', 'Origin': 'https://evil.invalid'}, method='POST')
        with self.assertRaises(HTTPError) as error:
            urlopen(wrong)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(len((self.game / 'scores.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
