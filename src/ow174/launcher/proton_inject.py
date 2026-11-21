"""Windows/Wine-side handle-bound relay injector for an active Wine session."""

import argparse
import os
import sys
from pathlib import Path

from ow174.launcher.inject import BoundProcess, ProcessExitedError, find_process_entries


def _same_windows_path(actual: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if not actual:
        return False
    return os.path.normcase(os.path.abspath(actual)) == os.path.normcase(os.path.abspath(expected))


def _describe_candidates(
    exe_name: str, expected_path: str | None
) -> tuple[list[tuple[int, str | None, int | None]], list[tuple[int, str]]]:
    """Inspect matching Windows processes once, returning (accessible, unreadable)."""
    found: list[tuple[int, str | None, int | None]] = []
    unreadable: list[tuple[int, str]] = []
    for pid, _name in find_process_entries(exe_name):
        try:
            with BoundProcess.open(pid) as proc:
                path = proc.image_path()
                if path is None:
                    unreadable.append((pid, "cannot query image path"))
                    continue
                if not _same_windows_path(path, expected_path):
                    continue
                ctime = proc.creation_time()
                if ctime is None:
                    unreadable.append((pid, "cannot query creation time"))
                    continue
                found.append((pid, path, ctime))
        except ProcessExitedError:
            continue
        except OSError as error:
            unreadable.append((pid, str(error)))
    return found, unreadable


def inject_bound_process(
    pid: int,
    dll_path: Path,
    *,
    expected_exe: str | None = None,
    expected_creation_time: int | None = None,
    timeout_ms: int = 10000,
    start_worker: bool = True,
) -> int:
    """Bind one handle to `pid`, verify identity, inject `dll_path`, and optionally start `worker`."""
    with BoundProcess.open(pid) as proc:
        actual_path = proc.image_path()
        if expected_exe and not _same_windows_path(actual_path, expected_exe):
            raise RuntimeError(
                f"Process PID {pid} image path {actual_path!r} does not match expected {expected_exe!r}"
            )
        if expected_creation_time is not None:
            actual_time = proc.creation_time()
            if actual_time != expected_creation_time:
                raise RuntimeError(
                    f"Process PID {pid} creation time {actual_time} does not match expected "
                    f"{expected_creation_time}; refusing to touch a replacement process"
                )
        base = proc.inject_dll(dll_path, timeout_ms=timeout_ms)
        if start_worker:
            proc.start_export(dll_path, base, b"worker")
        proc.require_alive("after relay injection")
        return base


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="ow174.launcher.proton_inject",
        description="Inspect or inject owwfd_relay.dll using a single retained Windows process handle.",
    )
    parser.add_argument("--exe-name", default="Overwatch.exe", help="process name to look up")
    parser.add_argument("--game-exe", help="expected full Windows path to the game executable")
    parser.add_argument("--win-pid", type=int, help="explicit Windows PID to bind and inject")
    parser.add_argument(
        "--creation-time", type=int, help="expected Windows FILETIME creation timestamp for --win-pid"
    )
    parser.add_argument("--dll", type=Path, help="path to owwfd_relay.dll")
    parser.add_argument("--timeout-ms", type=int, default=10000, help="remote LoadLibraryW timeout in ms")
    parser.add_argument("--list", action="store_true", help="list matching Windows processes and exit")
    parser.add_argument("--no-worker", action="store_true", help="skip explicit remote worker() start")
    args = parser.parse_args(argv)

    if args.list:
        candidates, unreadable = _describe_candidates(args.exe_name, args.game_exe)
        for pid, reason in unreadable:
            print(f"warning: win_pid={pid} unreadable ({reason})", file=sys.stderr)
        if not candidates:
            print(f"No matching {args.exe_name} processes found.", file=sys.stderr)
            return 1
        for pid, path, created in candidates:
            print(f"win_pid={pid}\tcreation_time={created}\tpath={path or '<unknown>'}")
        return 1 if unreadable else 0

    if args.dll is None:
        parser.error("--dll is required unless --list is set")

    if args.win_pid is None or args.creation_time is None or not args.game_exe:
        parser.error(
            "Explicit target identity is required for injection: pass --win-pid, "
            "--creation-time, and --game-exe (run with --list first to inspect candidates)."
        )

    try:
        base = inject_bound_process(
            args.win_pid,
            args.dll,
            expected_exe=args.game_exe,
            expected_creation_time=args.creation_time,
            timeout_ms=args.timeout_ms,
            start_worker=not args.no_worker,
        )
    except TimeoutError as error:
        print(f"Injection timed out (outcome unknown): {error}", file=sys.stderr)
        return 3
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Injection failed: {error}", file=sys.stderr)
        return 1

    worker_note = " and started worker thread" if not args.no_worker else ""
    print(
        f"Loaded {args.dll} into win_pid={args.win_pid} at base={hex(base)}{worker_note} "
        "(target process alive at check; verify relay readiness in game/logs)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
