"""Run trusted prepared-runtime unittests with a public summary and a private local log."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ID = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
PLATFORM_LINE = re.compile(rf"({TEST_ID})(?:\s+\[([a-z0-9_-]+)\])?", re.ASCII)


def platform_tag():
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"{sys.platform}-{arch}"


def read_baseline(path):
    result = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.partition("#")[0].strip()
        if entry:
            match = PLATFORM_LINE.fullmatch(entry)
            if not match:
                raise ValueError("invalid baseline entry")
            if match[2] in (None, platform_tag()):
                result.add(match[1])
    return result


def test_id(test):
    # A fixture error has no TestCase: unittest calls it 'setUpClass (module.Class)'.
    name = test.id()
    fixture = re.fullmatch(
        rf"(setUpClass|tearDownClass|setUpModule|tearDownModule) \(({TEST_ID})\)", name, re.ASCII
    )
    if fixture:
        return f"{fixture[2]}.{fixture[1]}"
    if not re.fullmatch(TEST_ID, name, re.ASCII):
        raise ValueError("test ID cannot be included in the public summary")
    return name


class Result(unittest.TextTestResult):
    """Count test methods once; parameter values from subtests stay in the private log."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = set()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.add(test_id(test))

    def summary(self):
        def ids(cases):
            return {test_id(getattr(test, "test_case", test)) for test in cases}

        errors = ids(test for test, _ in self.errors)
        failed = (ids(test for test, _ in self.failures) | ids(self.unexpectedSuccesses)) - errors
        skipped = ids(test for test, _ in self.skipped) - errors - failed
        expected = ids(test for test, _ in self.expectedFailures)
        passed = self.passed - skipped - errors - failed
        return {key: sorted(value) for key, value in (
            ("passed", passed), ("failed", failed), ("errors", errors), ("skipped", skipped),
            ("expected_failures", expected),
        )}


def run_worker(destination, pattern, filters):
    sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    loader.testNamePatterns = [f"*{item}*" if "*" not in item else item for item in filters] or None
    suite = loader.discover(str(ROOT / "tests/runtime"), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    destination.write_text(json.dumps(result.summary()), encoding="utf-8")


def save_baseline(path, failed):
    # Replace only after the entire suite and baseline write have succeeded.
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        tmp = Path(directory) / "baseline"
        tmp.write_text("".join(f"{item}\n" for item in sorted(failed)), encoding="utf-8")
        tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=os.environ.get("ORPHEUS_RUNTIME_DIR") or None)
    parser.add_argument("--log", type=Path, help="private local log (default: a new file under logs/)")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--max-skips", type=int)
    parser.add_argument("--pattern", default="test*.py", help="unittest discovery filename glob")
    parser.add_argument("-k", dest="filters", action="append", default=[], help="repeatable test name filter")
    args = parser.parse_args()
    if not args.runtime_dir:
        print("Set ORPHEUS_RUNTIME_DIR or pass --runtime-dir to a prepared runtime.", file=sys.stderr)
        return 2
    runtime = args.runtime_dir.resolve()
    manifest = runtime / "manifest.json"
    if args.max_skips is not None and args.max_skips < 0:
        raise ValueError("invalid skip limit")
    paths = [p.resolve() for p in (args.log, args.baseline, args.write_baseline, manifest) if p]
    if len(paths) != len(set(paths)):
        raise ValueError("input and output paths must be distinct")
    known = read_baseline(args.baseline) if args.baseline else set()
    env = dict(os.environ, ORPHEUS_RUNTIME_DIR=str(runtime))
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT / "src"), env.get("PYTHONPATH"))))
    with manifest.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    log_path = args.log
    if not log_path:
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"runtime-{uuid.uuid4().hex}.log"
    # Full logs can include prepared data. Restrict permissions when creating a log on POSIX runners.
    with (
        open(log_path, "wb", opener=lambda path, flags: os.open(path, flags, 0o600)) as log,
        tempfile.TemporaryDirectory() as private,
    ):
        options = {"cwd": ROOT, "env": env, "stdout": log, "stderr": subprocess.STDOUT}
        if subprocess.run([sys.executable, "-B", "-m", "orpheus", "--check-runtime"], **options).returncode:
            print("Runtime preflight failed; check the private log and prepared runtime.", file=sys.stderr)
            return 2
        report = Path(private) / "result.json"
        start = time.monotonic()
        cmd = [sys.executable, "-B", __file__, "--worker", str(report), args.pattern, *args.filters]
        if subprocess.run(cmd, **options).returncode or not report.is_file():
            print("Runtime suite did not finish; check the private log.", file=sys.stderr)
            return 2
        results = json.loads(report.read_text(encoding="utf-8"))
    failed = set(results["failed"]) | set(results["errors"])
    total = sum(map(len, results.values()))
    if not total:
        print("No runtime tests matched the selection.", file=sys.stderr)
        return 2
    new = failed - known
    fixed = known.intersection(results["passed"])
    if args.write_baseline:
        save_baseline(args.write_baseline, failed)
    counts = " ".join(f"{key}={len(values)}" for key, values in results.items())
    print(f"total={total} {counts} elapsed={time.monotonic() - start:.2f}s manifest_sha256={fingerprint}")
    for item in sorted(new):
        print(f"new failure: {item}")
    for item in sorted(fixed):
        print(f"fixed (remove from baseline): {item}")
    return int(bool(new) or (args.max_skips is not None and len(results["skipped"]) > args.max_skips))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        run_worker(Path(sys.argv[2]), sys.argv[3], sys.argv[4:])
    else:
        try:
            raise SystemExit(main())
        except (OSError, ValueError):
            print("Runtime test setup failed; check the runtime, baseline and output paths.", file=sys.stderr)
            raise SystemExit(2) from None
