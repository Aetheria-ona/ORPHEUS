"""Exercise the runtime CLI as a subprocess, with a synthetic pack, preflight and suite."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.run_runtime_tests import platform_tag

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_runtime_tests.py"
PRIVATE = "private-runtime-marker"


class RuntimeSuiteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.script = self.root / "scripts" / SCRIPT.name
        self.script.parent.mkdir()
        shutil.copyfile(SCRIPT, self.script)
        src = self.root / "src"
        src.mkdir()
        (src / "orpheus.py").write_text(textwrap.dedent("""
            import os, sys
            from pathlib import Path
            print('private-runtime-marker: preflight', flush=True)
            assert sys.argv[1:] == ['--check-runtime']
            sys.exit(0 if (Path(os.environ['ORPHEUS_RUNTIME_DIR']) / 'prepared').exists() else 1)
        """), encoding="utf-8")
        self.pack = self.root / "pack"
        self.pack.mkdir()
        (self.pack / "manifest.json").write_bytes(b"{}")
        (self.pack / "prepared").touch()
        suite = self.root / "tests/runtime"
        suite.mkdir(parents=True)
        (suite / "test_fake.py").write_text(textwrap.dedent("""
            import os, subprocess, sys, unittest
            class Case(unittest.TestCase):
                def test_pass(self):
                    os.write(2, b'private-runtime-marker: stderr\\n')
                    child = [sys.executable, '-c', 'print("private-runtime-marker: child")']
                    subprocess.run(child, check=True)
                def test_sub(self):
                    for n in range(2):
                        with self.subTest(secret=f'private-runtime-marker: {n}'):
                            self.fail('private-runtime-marker: failure')
                def test_err(self):
                    raise RuntimeError('private-runtime-marker: traceback')
                @unittest.skip('private-runtime-marker: skip')
                def test_skip(self):
                    pass
        """), encoding="utf-8")
        self.env = os.environ.copy()
        self.env.pop("ORPHEUS_RUNTIME_DIR", None)
        self.log = self.root / "private.log"
        self.baseline = self.root / "baseline.txt"

    def cli(self, *args, pack=True):
        opts = ["--runtime-dir", str(self.pack)] if pack else []
        run = subprocess.run([sys.executable, "-B", str(self.script), "--log", str(self.log), *opts, *args],
                             env=self.env, cwd=self.root, capture_output=True, text=True, check=False)
        console = run.stdout + run.stderr
        self.assertNotIn(PRIVATE, console)
        self.assertNotIn(str(self.root), console)
        return run.returncode, console

    def test_preflight_setup_errors_are_one_public_line(self):
        code, console = self.cli(pack=False)
        self.assertEqual((code, len(console.splitlines())), (2, 1))
        self.env["ORPHEUS_RUNTIME_DIR"] = str(self.pack)
        (self.pack / "prepared").unlink()
        code, console = self.cli(pack=False)
        self.assertEqual((code, len(console.splitlines())), (2, 1))
        self.assertIn("preflight", self.log.read_text(encoding="utf-8"))

    def test_baseline_and_private_subprocess_log(self):
        code, console = self.cli("--write-baseline", self.baseline)
        self.assertEqual(code, 1)
        self.assertIn("total=4 passed=1 failed=1 errors=1 skipped=1", console)
        self.assertIn(hashlib.sha256(b"{}").hexdigest(), console)
        self.assertEqual(self.baseline.read_text().splitlines(),
                         ["test_fake.Case.test_err", "test_fake.Case.test_sub"])
        private = self.log.read_text(encoding="utf-8")
        for marker in ("stderr", "child", "failure", "traceback", "preflight"):
            self.assertIn(f"{PRIVATE}: {marker}", private)
        self.baseline.write_text(
            f"test_fake.Case.test_err [{platform_tag()}] # temporary\n"
            "test_fake.Case.test_sub # shared\n"
            "test_fake.Case.test_pass\n"
            "test_fake.Case.test_skip\n"
            "test_fake.Case.test_absent\n"
        )
        code, console = self.cli("--baseline", self.baseline, "--max-skips", "1")
        self.assertEqual(code, 0)
        self.assertIn("fixed (remove from baseline): test_fake.Case.test_pass", console)
        self.assertNotIn("test_skip", console)
        self.assertNotIn("test_absent", console)
        self.assertEqual(self.cli("--baseline", self.baseline, "--max-skips", "0")[0], 1)

    def test_selection_keeps_unexecuted_baseline_entries_and_rejects_empty_suite(self):
        self.baseline.write_text("test_fake.Case.test_pass\ntest_fake.Case.test_sub [other-platform]\n")
        code, console = self.cli("--pattern", "test_f*.py", "-k", "test_su", "--baseline", self.baseline)
        self.assertEqual(code, 1)
        self.assertIn("total=1", console)
        self.assertIn("new failure: test_fake.Case.test_sub", console)
        self.assertNotIn("test_pass", console)
        code, _ = self.cli("--pattern", "no_match.py", "--write-baseline", self.baseline)
        self.assertEqual(code, 2)
        self.assertIn("test_pass", self.baseline.read_text())
