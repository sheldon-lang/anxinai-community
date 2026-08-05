import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, "-m", "opf", *args],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


class CLITests(unittest.TestCase):
    def test_default_redact_command(self):
        result = run_cli("手机号13800138000")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "手机号\n")

    def test_explicit_redact_json(self):
        result = run_cli(
            "redact",
            "联系人张伟",
            "--format",
            "json",
            "--json-indent",
            "0",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["detected_spans"][0]["label"], "private_person")

    def test_stdin(self):
        result = run_cli("--format", "text", stdin="邮箱a@example.com")
        self.assertEqual(result.stdout, "邮箱\n")


if __name__ == "__main__":
    unittest.main()

