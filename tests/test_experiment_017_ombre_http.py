"""Offline regression coverage for experiment 017's Ombre HTTP wiring."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_experiment_module():
    path = Path(__file__).resolve().parents[1] / "experiments" / "memory-quality-017" / "run_experiment.py"
    spec = importlib.util.spec_from_file_location("experiment_017_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


experiment = load_experiment_module()


class FakeResident:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeBackend:
    def close(self):
        pass


class FakeMemory:
    def __init__(self, *_args):
        self.state = {"entries": {}}

    def __exit__(self, *_args):
        pass

    def bucket_details(self, *_args, **_kwargs):
        raise AssertionError("empty fixture must not query Ombre")


class FakeOpenAI:
    def __init__(self, **_kwargs):
        pass

    def close(self):
        pass


class UnusedProxy:
    base = "http://127.0.0.1:49124"


class Experiment017OmbreHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".local-tmp")
        self.addCleanup(patch.stopall)
        self.addCleanup(self.temp.cleanup)

    def test_experiment_connects_to_isolated_token_authenticated_http_endpoint(self):
        resident = FakeResident()
        backend = FakeBackend()
        seen = {}

        def start(_root, cfg, *, data_root):
            seen["data_root"] = Path(data_root)
            seen["port"] = cfg.memory["ombre_port"]
            return 49123, "fictional-test-token", resident

        patches = [
            patch.object(experiment, "RUN", Path(self.temp.name)),
            patch("ombre_resident.start_or_connect", side_effect=start),
            patch("memory_agent.MCP", side_effect=lambda url, token: seen.update(url=url, token=token) or backend),
            patch("memory_agent.Memory", FakeMemory),
            patch("openai.OpenAI", FakeOpenAI),
        ]
        for item in patches:
            item.start()
        runtime = experiment.ombre_context(UnusedProxy(), "peter", transcripts=None)
        self.assertEqual(seen["url"], "http://127.0.0.1:49123/mcp")
        self.assertEqual(seen["token"], "fictional-test-token")
        self.assertEqual(seen["data_root"], Path(self.temp.name) / "ombre" / "peter" / "data")
        self.assertGreater(seen["port"], 0)
        experiment.close_ombre(runtime)
        self.assertTrue(resident.closed)

    def test_http_connection_failure_stops_only_the_isolated_resident(self):
        resident = FakeResident()
        patches = [
            patch.object(experiment, "RUN", Path(self.temp.name)),
            patch("ombre_resident.start_or_connect", return_value=(49123, "fake-token", resident)),
            patch("memory_agent.MCP", side_effect=OSError("simulated connection failure")),
            patch("openai.OpenAI", FakeOpenAI),
        ]
        for item in patches:
            item.start()
        with self.assertRaisesRegex(RuntimeError, "HTTP MCP connection failed"):
            experiment.ombre_context(UnusedProxy(), "peter", transcripts=None)
        self.assertTrue(resident.closed)


if __name__ == "__main__":
    unittest.main()

