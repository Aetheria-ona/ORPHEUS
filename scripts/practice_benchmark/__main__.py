"""Run from a source checkout with python -m scripts.practice_benchmark."""

import argparse
import hashlib
import json
import logging
import math
import platform
import subprocess
import sys
import time
from pathlib import Path

from .metrics import write_ticks


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure one prepared Practice Range worker on loopback")
    parser.add_argument("--test-players", required=True, type=int, choices=(1, 2, 4, 8))
    parser.add_argument("--seconds", type=float, default=8, help="wall-clock observation window")
    parser.add_argument("--warmup-frames", type=int, default=112)
    parser.add_argument(
        "--out", type=Path, required=True, help="local directory for this run's CSV and summary"
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds <= 0 or args.warmup_frames < 0:
        parser.error("seconds must be positive and finite, and warmup frames must be non-negative")
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        parser.error("the output directory must be new or empty")
    # Select the checked-out server even if the active environment has a different editable install.
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "src"))
    from ow174.paths import MATCH_RUNTIME_DATA, RUNTIME_DIR

    if RUNTIME_DIR is None or not (RUNTIME_DIR / "manifest.json").is_file():
        parser.error("a prepared runtime with a manifest is required (ORPHEUS_RUNTIME_DIR)")
    if any(not (RUNTIME_DIR / "data" / entry).is_file() for entry in MATCH_RUNTIME_DATA):
        parser.error("the selected runtime is missing gameplay inputs")
    logging.getLogger("ow174").setLevel(logging.CRITICAL)
    from .worker import run

    try:
        rows, report = run(args.test_players, args.seconds, args.warmup_frames)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"benchmark failed: {error}\n")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    report.update(
        source_revision=revision.stdout.strip() or None,
        runtime_manifest_sha256=hashlib.sha256((RUNTIME_DIR / "manifest.json").read_bytes()).hexdigest(),
        platform=platform.platform(),
        machine=platform.machine(),
        python=platform.python_version(),
        cpu_clock=time.get_clock_info("thread_time").implementation,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    write_ticks(args.out / "ticks.csv", rows)
    with (args.out / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
