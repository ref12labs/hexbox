import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("relay", Path(__file__).parents[1] / "scripts/relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class RelayTests(unittest.TestCase):
    def test_body_both_platforms(self):
        for system in ("linux", "windows"):
            self.assertEqual(relay.request_body("ref12labs/webbox", "123", "456", system), {
                "name": "relay-123-456", "runner_group_id": 1,
                "labels": ["relay-" + system, "run-123"], "work_folder": "_work",
            })

    def test_invalid_inputs(self):
        for args in [
            ("other/private", "1", "2", "linux"),
            ("ref12labs/webbox", "0", "2", "linux"),
            ("ref12labs/webbox", "1\n", "2", "linux"),
            ("ref12labs/webbox", "1", "../../x", "linux"),
            ("ref12labs/webbox", "1", "2", "macos"),
        ]:
            with self.assertRaises(ValueError):
                relay.request_body(*args)

    def test_strip_outer_context(self):
        source = {
            "GITHUB_TOKEN": "secret", "ACTIONS_RUNTIME_TOKEN": "secret",
            "RUNNER_TRACKING_ID": "outer", "RELAY_GITHUB_TOKEN": "secret",
            "GH_TOKEN": "secret", "PATH": "/bin", "TEMP": "/tmp",
        }
        self.assertEqual(relay.child_environment(source), {"PATH": "/bin", "TEMP": "/tmp"})

    def test_http_request(self):
        class Response:
            def __enter__(self): return io.BytesIO(b'{"encoded_jit_config":"hidden"}')
            def __exit__(self, *args): pass
        with patch.object(relay.urllib.request, "urlopen", return_value=Response()) as send:
            body = relay.request_body("ref12labs/webbox", "12", "34", "linux")
            relay.api("fake", "/repos/ref12labs/webbox/actions/runners/generate-jitconfig", body)
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), body)
        self.assertEqual(request.get_header("Authorization"), "Bearer fake")

    @unittest.skipIf(os.name == "nt", "Linux fake runner")
    def test_runner_output_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "run.sh"
            script.write_text("""#!/bin/sh
echo 'private-output SECRET'
echo '::warning::private-action-command'
echo '2026-10-11 00:00:00Z: Running job: confidential-name'
echo 'diagnostics-secret' >&2
echo '2026-10-11 00:00:00Z: Job confidential-name completed with result: Succeeded'
""")
            script.chmod(0o755)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = relay.serve(root, "fake-jit", "linux", timeout=5)
            self.assertEqual(result, "Succeeded")
            self.assertEqual(output.getvalue(), "Private job started (output stays in private Actions logs)\n")

    @unittest.skipIf(os.name == "nt", "Linux fake runner")
    def test_runner_failure_and_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "run.sh"
            script.write_text("#!/bin/sh\nexit 2\n")
            script.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "code 2"):
                relay.serve(root, "fake", "linux", timeout=5)
            script.write_text("#!/bin/sh\nexec tail -f /dev/null\n")
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                relay.serve(root, "fake", "linux", timeout=0.1)

    def test_already_started_job_skips_without_jit(self):
        env = {"RELAY_REPO": "ref12labs/webbox", "RELAY_RUN_ID": "1",
               "RELAY_JOB_ID": "2", "RELAY_OS": "linux", "RELAY_GITHUB_TOKEN": "fake"}
        with patch.dict(os.environ, env), patch.object(relay, "api", return_value={
            "run_id": 1, "status": "completed"
        }) as api, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(relay.main(), 0)
            self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
