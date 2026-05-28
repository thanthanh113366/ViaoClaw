import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _config(**exec_overrides):
    base = {
        "exec": {
            "enabled": True,
            "workspace": "/tmp/unused",
            "timeout_seconds": 60,
            "allow_network": True,
            "deny_patterns": [r"rm\s+-rf", r"mkfs"],
            "max_output_bytes": 65536,
        }
    }
    base["exec"].update(exec_overrides)
    return base


class ExecRunnerTests(unittest.TestCase):
    _runner_cls = None

    @classmethod
    def setUpClass(cls):
        mock_logger = MagicMock()
        with patch("config.logger.setup_logging", return_value=mock_logger):
            from core.exec.runner import ExecRunner

            cls._runner_cls = ExecRunner

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config = _config(
            workspace=self.temp_dir, timeout_seconds=5, max_output_bytes=64
        )

    def test_deny_rm_rf(self):
        runner = self._runner_cls(self.config)
        with self.assertRaises(RuntimeError) as ctx:
            runner.run("rm -rf /")
        self.assertIn("deny pattern", str(ctx.exception))

    def test_run_echo(self):
        runner = self._runner_cls(self.config)
        output = runner.run("echo hello-exec")
        self.assertIn("hello-exec", output)

    def test_timeout(self):
        runner = self._runner_cls(
            _config(workspace=self.temp_dir, timeout_seconds=1)
        )
        with self.assertRaises(RuntimeError) as ctx:
            runner.run("sleep 3")
        self.assertIn("timed out", str(ctx.exception))

    def test_cwd_escape_rejected(self):
        runner = self._runner_cls(self.config)
        outside = os.path.abspath(
            os.path.join(self.temp_dir, "..", "outside_exec_test")
        )
        os.makedirs(outside, exist_ok=True)
        with self.assertRaises(RuntimeError) as ctx:
            runner.run("pwd", cwd=outside)
        self.assertIn("workspace", str(ctx.exception))

    def test_output_truncated(self):
        runner = self._runner_cls(
            _config(workspace=self.temp_dir, max_output_bytes=16, timeout_seconds=5)
        )
        output = runner.run("python3 -c \"print('x' * 200)\"")
        self.assertIn("truncated", output)

    def test_exec_enabled_flag(self):
        from core.exec.service import is_exec_enabled

        self.assertTrue(is_exec_enabled(_config(enabled=True)))
        self.assertFalse(is_exec_enabled(_config(enabled=False)))


if __name__ == "__main__":
    unittest.main()
