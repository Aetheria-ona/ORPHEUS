"""Linux Proton runtime discovery and explicit relay helper runner.

Resolves the active Proton runtime, WINEPREFIX, and Wine sync flags from a running
game process's /proc/<pid>/environ and /proc/<pid>/maps, failing closed when the
runtime or process selection is ambiguous.

Usage:
  1. List matching Windows processes in the active Proton session:
     python -m ow174.launcher.proton --list --win-python /path/to/winpy/python.exe
  2. Inject using the exact win_pid, creation_time, and path printed by --list:
     python -m ow174.launcher.proton --win-python /path/to/winpy/python.exe \
       --relay-dll relay/owwfd_relay.dll --win-pid <PID> \
       --creation-time <FILETIME> --game-exe '<WINDOWS_PATH>'
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ow174.launcher import LaunchError
from ow174.paths import RELAY_DLL, ROOT

DEFAULT_SRC = ROOT / "src"

SYNC_ENV_KEYS = (
    "WINEFSYNC",
    "WINEESYNC",
    "WINE_NTSYNC",
    "PROTON_ENABLE_NTSYNC",
    "WINEFUTEX2",
)
VALID_SYNC_VALUES = frozenset({"0", "1"})


@dataclass(frozen=True)
class ProtonRuntime:
    tool_dir: Path
    wine_bin: Path
    wineprefix: Path
    ld_library_path: str
    sync_env: dict[str, str]


def to_wine_path(path: Path) -> str:
    """Convert an absolute POSIX path to a Wine Z: drive path."""
    resolved = Path(os.path.abspath(path))
    parts = [part for part in resolved.parts if part != "/"]
    return str(PureWindowsPath("Z:\\", *parts))


def read_proc_environ(pid: int, proc_root: Path = Path("/proc")) -> dict[str, str]:
    """Read NUL-delimited environment variables from /proc/<pid>/environ."""
    env_file = proc_root / str(pid) / "environ"
    try:
        raw = env_file.read_bytes()
    except OSError as error:
        raise LaunchError(f"Cannot read environment of Linux PID {pid}: {error}") from error
    result: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key_b, val_b = entry.split(b"=", 1)
        result[key_b.decode("utf-8", "replace")] = val_b.decode("utf-8", "replace")
    return result


def find_linux_game_pids(exe_name: str = "Overwatch.exe", proc_root: Path = Path("/proc")) -> list[int]:
    """Return readable Linux PIDs whose comm or argv[0] basename matches `exe_name`."""
    wanted = exe_name.lower()
    pids: list[int] = []
    if not proc_root.is_dir():
        return pids
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            if comm.lower() == wanted:
                pids.append(pid)
                continue
            cmdline = (entry / "cmdline").read_bytes()
            if cmdline:
                argv0 = cmdline.split(b"\0", 1)[0].decode("utf-8", "replace")
                if Path(argv0.replace("\\", "/")).name.lower() == wanted:
                    pids.append(pid)
        except OSError:
            continue
    return sorted(pids)


def _maps_confirm_tool_files(pid: int, files_dir: Path, proc_root: Path) -> bool:
    maps_file = proc_root / str(pid) / "maps"
    try:
        maps_text = maps_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    prefix = str(files_dir) + "/"
    for line in maps_text.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        mapped = parts[5].strip()
        if mapped.endswith(" (deleted)"):
            mapped = mapped[: -len(" (deleted)")]
        if mapped.startswith(prefix):
            return True
    return False


def _select_tool_dir(pid: int, compat_paths: str, proc_root: Path) -> Path:
    candidates: list[Path] = []
    for raw in compat_paths.split(":"):
        if not raw:
            continue
        if not os.path.isabs(raw):
            raise LaunchError(
                f"Relative path {raw!r} in STEAM_COMPAT_TOOL_PATHS for Linux PID {pid}; refusing to guess."
            )
        tool = Path(raw)
        wine_bin = tool / "files" / "bin" / "wine"
        if wine_bin.is_file() and os.access(wine_bin, os.X_OK):
            resolved = Path(os.path.realpath(tool))
            if resolved not in candidates:
                candidates.append(resolved)
    if not candidates:
        raise LaunchError(
            f"No Proton runtime with files/bin/wine found in STEAM_COMPAT_TOOL_PATHS for PID {pid}."
        )
    mapped = [tool for tool in candidates if _maps_confirm_tool_files(pid, tool / "files", proc_root)]
    if len(mapped) == 1:
        return mapped[0]
    if not mapped:
        raise LaunchError(
            f"None of the STEAM_COMPAT_TOOL_PATHS candidates are mapped in /proc/{pid}/maps: "
            + ", ".join(str(c) for c in candidates)
        )
    raise LaunchError(
        f"Ambiguous Proton runtime for PID {pid}; multiple mapped candidates in STEAM_COMPAT_TOOL_PATHS: "
        + ", ".join(str(c) for c in mapped)
    )


def resolve_proton_runtime(pid: int, proc_root: Path = Path("/proc")) -> ProtonRuntime:
    """Resolve the active Proton tool, prefix, and Wine sync env from a running Linux PID."""
    env = read_proc_environ(pid, proc_root=proc_root)
    raw_prefix = env.get("WINEPREFIX", "").strip()
    if not raw_prefix:
        raise LaunchError(f"WINEPREFIX is missing in environment of Linux PID {pid}.")
    if not os.path.isabs(raw_prefix):
        raise LaunchError(f"Relative WINEPREFIX {raw_prefix!r} for Linux PID {pid}; refusing to guess.")
    wineprefix = Path(os.path.realpath(raw_prefix))
    if not wineprefix.is_dir():
        raise LaunchError(f"WINEPREFIX {wineprefix} for Linux PID {pid} does not exist.")

    compat_paths = env.get("STEAM_COMPAT_TOOL_PATHS", "")
    tool_dir = _select_tool_dir(pid, compat_paths, proc_root)
    files_dir = tool_dir / "files"
    wine_bin = files_dir / "bin" / "wine"

    sync_env: dict[str, str] = {}
    for key in SYNC_ENV_KEYS:
        if key in env:
            value = env[key]
            if value not in VALID_SYNC_VALUES:
                raise LaunchError(f"Unexpected {key}={value!r} in Linux PID {pid}; expected '0' or '1'.")
            sync_env[key] = value

    lib_dirs = [
        str(files_dir / rel)
        for rel in ("lib64", "lib", "lib/x86_64-linux-gnu")
        if (files_dir / rel).is_dir()
    ]
    return ProtonRuntime(
        tool_dir=tool_dir,
        wine_bin=wine_bin,
        wineprefix=wineprefix,
        ld_library_path=":".join(lib_dirs),
        sync_env=sync_env,
    )


def run_proton_helper(
    runtime: ProtonRuntime,
    win_python: Path,
    dll_path: Path | None = None,
    *,
    win_pid: int | None = None,
    creation_time: int | None = None,
    game_exe: str | None = None,
    list_only: bool = False,
    timeout: float = 30.0,
    no_worker: bool = False,
    src_root: Path = DEFAULT_SRC,
) -> subprocess.CompletedProcess[str]:
    """Run the Windows-side handle-bound injector inside the resolved Proton session."""
    if not win_python.is_file():
        raise LaunchError(f"Windows Python executable not found: {win_python}")
    if not list_only:
        if dll_path is None:
            raise LaunchError("Relay DLL path is required unless listing processes.")
        if not dll_path.is_file():
            raise LaunchError(f"Relay DLL not found: {dll_path}")

    bootstrap = (
        "import sys; "
        f"sys.path.insert(0, {to_wine_path(src_root)!r}); "
        "from ow174.launcher.proton_inject import main; "
        "raise SystemExit(main(sys.argv[1:]))"
    )
    cmd = [
        str(runtime.wine_bin),
        to_wine_path(win_python),
        "-I",
        "-B",
        "-c",
        bootstrap,
    ]
    if list_only:
        cmd.append("--list")
    else:
        assert dll_path is not None
        cmd.extend(["--dll", to_wine_path(dll_path)])
    if win_pid is not None:
        cmd.extend(["--win-pid", str(win_pid)])
    if creation_time is not None:
        cmd.extend(["--creation-time", str(creation_time)])
    if game_exe:
        cmd.extend(["--game-exe", game_exe])
    if no_worker:
        cmd.append("--no-worker")

    env = {k: v for k, v in os.environ.items() if k not in SYNC_ENV_KEYS}
    env.update(runtime.sync_env)
    env["WINEPREFIX"] = str(runtime.wineprefix)
    if runtime.ld_library_path:
        env["LD_LIBRARY_PATH"] = runtime.ld_library_path

    if not (timeout > 0 and timeout != float("inf")):
        raise LaunchError(f"Timeout must be positive and finite, got {timeout!r}.")
    try:
        return subprocess.run(
            cmd,
            env=env,
            cwd=win_python.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise LaunchError(
            f"Proton helper timed out after {timeout:.1f}s; outcome unknown "
            "(existing Wine session and wineserver left untouched)."
        ) from error
    except OSError as error:
        raise LaunchError(f"Failed to start Proton helper ({runtime.wine_bin}): {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ow174.launcher.proton",
        description=(
            "Inspect or inject owwfd_relay.dll in an active Proton Wine session. "
            "--runtime-pid only selects WINEPREFIX/Proton/sync env; it does not bind a Linux PID "
            "to a Windows PID. Run --list first, then pass --win-pid, --creation-time, and --game-exe."
        ),
    )
    parser.add_argument(
        "--runtime-pid",
        type=int,
        help="Linux PID used ONLY to resolve WINEPREFIX, Proton runtime, and Wine sync env",
    )
    parser.add_argument("--win-pid", type=int, help="explicit Windows PID inside the Wine session")
    parser.add_argument(
        "--creation-time", type=int, help="expected Windows FILETIME creation timestamp for --win-pid"
    )
    parser.add_argument("--game-exe", help="expected full Windows path to Overwatch.exe (required to inject)")
    parser.add_argument(
        "--win-python",
        type=Path,
        default=Path(os.environ["ORPHEUS_WIN_PYTHON"]) if "ORPHEUS_WIN_PYTHON" in os.environ else None,
        help="path to 64-bit Windows python.exe (or set ORPHEUS_WIN_PYTHON)",
    )
    parser.add_argument("--relay-dll", type=Path, default=RELAY_DLL, help="path to owwfd_relay.dll")
    parser.add_argument("--timeout", type=float, default=30.0, help="helper timeout in seconds")
    parser.add_argument("--list", action="store_true", help="list Windows processes in the Proton session")
    parser.add_argument("--no-worker", action="store_true", help="skip explicit remote worker() start")
    args = parser.parse_args(argv)

    if args.win_python is None:
        print("Error: pass --win-python PATH or set ORPHEUS_WIN_PYTHON.", file=sys.stderr)
        return 2

    if not args.list and (args.win_pid is None or args.creation_time is None or not args.game_exe):
        print(
            "Error: injection requires explicit --win-pid, --creation-time, and --game-exe "
            "(run --list first; --runtime-pid only resolves the Wine environment).",
            file=sys.stderr,
        )
        return 2

    runtime_pid = args.runtime_pid
    if runtime_pid is None:
        pids = find_linux_game_pids()
        if not pids:
            print("Error: no running Overwatch.exe Linux process found.", file=sys.stderr)
            return 1
        if len(pids) > 1:
            print(
                f"Error: multiple Overwatch.exe Linux processes found ({pids}); pass --runtime-pid.",
                file=sys.stderr,
            )
            return 2
        runtime_pid = pids[0]

    try:
        runtime = resolve_proton_runtime(runtime_pid)
        result = run_proton_helper(
            runtime,
            args.win_python,
            None if args.list else args.relay_dll,
            win_pid=args.win_pid,
            creation_time=args.creation_time,
            game_exe=args.game_exe,
            list_only=args.list,
            timeout=args.timeout,
            no_worker=args.no_worker,
        )
    except LaunchError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
