import base64
import io
import os
import tempfile
import threading
import unittest
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from lab_preview import LabPreviewResult
from lab_preview import (
    IMAGE_PROMPT,
    MAX_ITERATIONS,
    MAX_RESOURCE_REQUESTS,
    LabPreview,
    LabPreviewResult,
    _make_handler,
    _safe_lab_file,
)
from tools_agent import LAB_ONLY, permitted_tools, tool_policy
from veil_agent import Budget, Chat, Store, load_character
from windows_observer import observation_image_message


class LabPreviewPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lab = self.root / "lab"
        self.lab.mkdir()
        (self.lab / "game.html").write_text("<h1>game</h1>", encoding="utf-8")
        (self.lab / "style.css").write_text("h1 { color: red; }", encoding="utf-8")

    def test_only_regular_html_entries_and_supported_static_resources_resolve(self):
        self.assertEqual(_safe_lab_file(self.lab, "game.html", page=True), self.lab / "game.html")
        self.assertEqual(_safe_lab_file(self.lab, "style.css"), self.lab / "style.css")
        for path in ("../outside.html", "C:/outside.html", "/outside.html", "game.js", "game.exe",
                     "missing.html", "..\\outside.html", "folder//game.html"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                _safe_lab_file(self.lab, path, page=True)

    def test_every_reparse_point_in_a_page_path_is_rejected(self):
        page = self.lab / "game.html"
        with patch("lab_preview._is_reparse_point", side_effect=lambda path: Path(path) == page):
            with self.assertRaisesRegex(ValueError, "Links and junctions"):
                _safe_lab_file(self.lab, "game.html", page=True)

    def test_lab_root_cannot_itself_be_a_link_or_junction(self):
        with patch("lab_preview._is_reparse_point", return_value=True):
            with self.assertRaisesRegex(ValueError, "configured Lab folder"):
                LabPreview(self.lab, platform_enabled=True)


class LabPreviewServerTests(unittest.TestCase):
    def test_temporary_server_serves_only_lab_files_with_a_browser_sandbox_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lab = root / "lab"
            lab.mkdir()
            (lab / "index.html").write_text("<h1>game</h1>", encoding="utf-8")
            (root / "private.txt").write_text("private", encoding="utf-8")
            failures = []
            handler_type = _make_handler(lab, [0], failures)

            def request(path, *, head=False):
                handler = object.__new__(handler_type)
                handler.path = path
                handler.wfile = io.BytesIO()
                headers = {}
                status = []
                handler.send_response = status.append
                handler.send_header = headers.__setitem__
                handler.end_headers = lambda: None
                handler.send_error = lambda code: status.append(code)
                handler._serve(head=head)
                return status[-1], headers, handler.wfile.getvalue()

            status, headers, body = request("/index.html")
            self.assertEqual(status, 200)
            self.assertIn(b"game", body)
            self.assertIn("sandbox allow-scripts allow-same-origin", headers["Content-Security-Policy"])
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")

            outside_status, _, _ = request("/%2e%2e/private.txt")
            self.assertEqual(outside_status, 404)
            self.assertEqual(failures, ["Missing or rejected Lab resource: /%2e%2e/private.txt"])

    def test_temporary_server_caps_resource_requests_per_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            lab = Path(temporary) / "lab"
            lab.mkdir()
            (lab / "index.html").write_text("ok", encoding="utf-8")
            failures = []
            handler_type = _make_handler(lab, [0], failures)

            def request():
                handler = object.__new__(handler_type)
                handler.path = "/index.html"
                handler.wfile = io.BytesIO()
                status = []
                handler.send_response = status.append
                handler.send_header = lambda *_args: None
                handler.end_headers = lambda: None
                handler.send_error = lambda code: status.append(code)
                handler._serve(head=False)
                return status[-1]

            for _ in range(MAX_RESOURCE_REQUESTS):
                self.assertEqual(request(), 200)
            self.assertEqual(request(), 429)


class LabPreviewToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.lab = Path(self.temporary.name) / "lab"
        self.lab.mkdir()
        (self.lab / "game.html").write_text("<button>go</button>", encoding="utf-8")

    def test_preview_is_lab_scoped_two_per_request_and_sends_image_only_as_model_content(self):
        png = b"fake jpeg bytes"
        calls = []

        def render(lab, entry, stopped):
            calls.append((lab, entry, stopped()))
            return {
                "loaded": True, "reason": "", "errors": ["JavaScript exception: test"],
                "blocked": ["Blocked external request: https://example.invalid"],
                "resource_failures": [], "screenshot": png,
            }

        preview = LabPreview(self.lab, platform_enabled=True, runner=render)
        self.assertEqual(tool_policy("preview_lab_page"), "lab")
        self.assertIn("preview_lab_page", LAB_ONLY)
        self.assertEqual(permitted_tools(preview.tools)[0]["function"]["name"], "preview_lab_page")

        result, state, _ = preview.invoke({"path": "lab/game.html"})
        self.assertEqual(state, "成功")
        self.assertTrue(result.image_data_uri.startswith("data:image/jpeg;base64,"))
        self.assertIn("Page loaded: yes", result)
        self.assertIn("JavaScript exception", result)
        self.assertNotIn("fake jpeg bytes", str(result))
        self.assertEqual(base64.b64decode(result.image_data_uri.partition(",")[2]), png)

        image_message = observation_image_message(result)
        self.assertIsNotNone(image_message)
        self.assertEqual(image_message["role"], "user")
        self.assertEqual(image_message["content"][0]["text"], IMAGE_PROMPT)
        self.assertEqual(image_message["content"][1]["image_url"]["url"], result.image_data_uri)

        preview.invoke({"path": "lab/game.html"})
        denied, denied_state, _ = preview.invoke({"path": "lab/game.html"})
        self.assertEqual(denied_state, "被拒绝")
        self.assertIn("two attempts", denied)
        self.assertEqual(len(calls), MAX_ITERATIONS)
        preview.begin_request()
        self.assertEqual(preview.invoke({"path": "lab/game.html"})[1], "成功")

    def test_invalid_input_and_non_windows_platform_fail_closed(self):
        preview = LabPreview(self.lab, platform_enabled=False, runner=lambda *_: self.fail("runner called"))
        self.assertEqual(preview.invoke({"path": "lab/game.html"})[1], "被拒绝")
        preview.platform_enabled = True
        self.assertEqual(preview.invoke({"path": "../outside.html"})[1], "被拒绝")
        self.assertEqual(preview.invoke({"path": "lab/game.js"})[1], "被拒绝")
        self.assertEqual(preview.invoke({"path": "lab/game.html", "extra": True})[1], "被拒绝")

    def test_timeout_is_reported_without_blocking_the_caller(self):
        def slow(*_args):
            threading.Event().wait(0.2)
            return {"loaded": True, "errors": [], "blocked": [], "resource_failures": [], "screenshot": b"x"}

        preview = LabPreview(self.lab, platform_enabled=True, timeout_seconds=0.02, runner=slow)
        result, state, _ = preview.invoke({"path": "lab/game.html"})
        self.assertEqual(state, "超时")
        self.assertIn("timed out", result)


class LabPreviewChatImageTests(unittest.TestCase):
    def test_preview_image_is_sent_to_the_current_model_and_never_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "characters" / "test").mkdir(parents=True)
            (root / "persona").mkdir()
            (root / "persona" / "test.md").write_text("Fictional persona", encoding="utf-8")
            (root / "characters" / "test" / "character.toml").write_text('''name = "Test"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 80
[providers.fake]
model = "fake-vision-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
''', encoding="utf-8")
            cfg = replace(load_character(root, "test"), tools={
                "enabled": True, "folder": "data/test/workspace", "max_steps": 3,
                "max_result_bytes": 8192,
            })
            store = Store(root / "data")
            image_uri = "data:image/jpeg;base64," + base64.b64encode(b"transient preview").decode("ascii")
            call = SimpleNamespace(id="preview-call", function=SimpleNamespace(
                name="preview_lab_page", arguments=json.dumps({"path": "lab/game.html"})))
            first = SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda exclude_none=True: {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "preview-call", "type": "function", "function": {
                        "name": "preview_lab_page", "arguments": call.function.arguments}}]})
            final = SimpleNamespace(content="I can see the rendered Lab page.", tool_calls=[],
                                    model_dump=lambda exclude_none=True: {
                                        "role": "assistant", "content": "I can see the rendered Lab page."})
            requests = []

            class Tools:
                tools = [SimpleNamespace(name="preview_lab_page", description="preview", inputSchema={
                    "type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"], "additionalProperties": False,
                })]
                capability_event = None
                capability_event_model = None

                def begin_request(self):
                    return None

                def preview_summary(self, name, args):
                    return args.get("path", name)

                def invoke(self, name, args, approval, *, should_stop=None):
                    return LabPreviewResult("Page loaded: yes. Screenshot captured: yes.", image_uri), "成功", args["path"]

            def model(messages, **kwargs):
                requests.append([dict(message) for message in messages])
                return (first if len(requests) == 1 else final), 100, 20, 0, "stop"

            chat = Chat(cfg, store, Budget(store), Mock(side_effect=model), tools=Tools(), output=lambda _: None)
            reply = chat.send("Preview my Lab page.")

            self.assertEqual(reply, "I can see the rendered Lab page.")
            self.assertEqual(len(requests), 2)
            images = [message for message in requests[1]
                      if message.get("role") == "user" and isinstance(message.get("content"), list)
                      and any(part.get("type") == "image_url" for part in message["content"])]
            self.assertEqual(len(images), 1)
            self.assertIn("Peter's own Lab page", images[0]["content"][0]["text"])
            self.assertIn("untrusted data", images[0]["content"][0]["text"])
            self.assertEqual(images[0]["content"][1]["image_url"]["url"], image_uri)
            self.assertNotIn(image_uri, str(store.read("test/chat.jsonl")))
            self.assertNotIn(image_uri, str(store.read("test/tools.jsonl")))

            class VisionRejected(Exception):
                status_code = 400
                message = "This model does not support image input."

            requests.clear()

            def rejecting_model(messages, **kwargs):
                requests.append([dict(message) for message in messages])
                if len(requests) == 2:
                    raise VisionRejected()
                return (first if len(requests) == 1 else final), 100, 20, 0, "stop"

            chat.model = rejecting_model
            reply = chat.send("Preview this Lab page again.")
            self.assertEqual(reply, "I can see the rendered Lab page.")
            self.assertEqual(len(requests), 3)
            self.assertTrue(any(item.get("type") == "image_url"
                                for message in requests[1] if isinstance(message.get("content"), list)
                                for item in message["content"]))
            self.assertFalse(any(item.get("type") == "image_url"
                                 for message in requests[2] if isinstance(message.get("content"), list)
                                 for item in message["content"]))
            self.assertTrue(any(message.get("role") == "system"
                                and "Peter has not seen or visually verified" in message.get("content", "")
                                for message in requests[2]))
            self.assertTrue(any(message.get("role") == "tool"
                                and "Peter has not seen the page" in message.get("content", "")
                                for message in requests[2]))

    def test_image_rejection_is_reported_as_not_visually_verified(self):
        class ProviderError(Exception):
            status_code = 400
            message = "This model does not support image input."

        self.assertTrue(Chat._is_image_input_rejection(ProviderError()))
        self.assertFalse(Chat._is_image_input_rejection(RuntimeError("connection timed out")))


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("VEIL_LAB_PREVIEW_BROWSER_TEST") == "1",
    "Run in the Windows browser integration CI job.",
)
class LabPreviewChromiumTests(unittest.TestCase):
    def test_real_chromium_renders_and_reports_script_resource_and_network_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            lab = Path(temporary) / "lab"
            lab.mkdir()
            (lab / "index.html").write_text("""<!doctype html><html><head>
<link rel="stylesheet" href="missing.css">
<style>body { background: #123456; color: white; }</style></head><body>
<h1>Lab preview</h1><script>
if (document.cookie || localStorage.getItem('profile')) throw new Error('private browser data leaked');
document.cookie = 'private=local-only';
localStorage.setItem('profile', 'must-not-persist');
fetch('https://example.invalid/data').catch(() => {});
fetch('file:///C:/Windows/win.ini').catch(() => {});
setTimeout(() => { throw new Error('deliberate preview error'); }, 10);
</script></body></html>""", encoding="utf-8")
            preview = LabPreview(lab, platform_enabled=True)
            result, state, _ = preview.invoke({"path": "lab/index.html"})
            self.assertEqual(state, "成功", str(result))
            self.assertIn("Page loaded: yes", result)
            self.assertIn("JavaScript exception", result)
            self.assertIn("missing.css", result)
            self.assertIn("example.invalid", result)
            self.assertIsNotNone(observation_image_message(result))
            second, second_state, _ = preview.invoke({"path": "lab/index.html"})
            self.assertEqual(second_state, "成功", str(second))
            self.assertNotIn("private browser data leaked", second)
            self.assertFalse(list(lab.glob("*.jpg")))
            self.assertFalse(list(lab.glob("*.png")))


if __name__ == "__main__":
    unittest.main()
