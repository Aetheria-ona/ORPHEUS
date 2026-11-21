"""Synthetic CI-safe tests for handle-bound injection and Linux Proton runtime resolution."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ow174.launcher import LaunchError, inject, proton, proton_inject


class FakeBoundKernel:
    """Minimal kernel32 mock where handle 0x100000001 is the process and 0x200000002 is a thread."""

    def __init__(self, *, alive=True, thread_wait=0, worker_wait=258, worker_code=0, created_lo=1234):
        self.alive = alive
        self.thread_wait = thread_wait
        self.worker_wait = worker_wait
        self.worker_code = worker_code
        self.created_lo = created_lo
        self.opens = 0
        self.closed = []
        self.freed = []
        self.remote_threads = []
        self.freed_libs = []

    def OpenProcess(self, access, inherit, pid):
        self.opens += 1
        return 0x100000001

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1

    def WaitForSingleObject(self, handle, ms):
        if handle == 0x100000001:
            return inject.WAIT_TIMEOUT if self.alive else inject.WAIT_OBJECT_0
        if handle == 0x200000002:
            return self.thread_wait
        if handle == 0x300000003:
            return self.worker_wait
        return inject.WAIT_FAILED

    def QueryFullProcessImageNameW(self, handle, flags, buf, size):
        buf.value = "C:\\Games\\Overwatch\\_retail_\\Overwatch.exe"
        return 1

    def GetProcessTimes(self, handle, created, exited, kernel, user):
        created._obj.dwLowDateTime = self.created_lo
        created._obj.dwHighDateTime = 0
        return 1

    def VirtualAllocEx(self, handle, addr, size, alloc_type, protect):
        return 0x700000000

    def WriteProcessMemory(self, handle, remote, data, size, written):
        written._obj.value = size
        return 1

    def CreateRemoteThread(self, handle, sa, stack, start, param, flags, tid):
        self.remote_threads.append((handle, start, param))
        if param is None:
            return 0x300000003
        return 0x200000002

    def GetExitCodeThread(self, handle, code):
        code._obj.value = self.worker_code if handle == 0x300000003 else 0
        return 1

    def VirtualFreeEx(self, handle, remote, size, kind):
        self.freed.append(remote)
        return 1

    def LoadLibraryExW(self, path, file_handle, flags):
        return 0x400000000

    def GetProcAddress(self, module, name):
        return 0x400001234

    def FreeLibrary(self, module):
        self.freed_libs.append(module)
        return 1


class BoundProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ow-bound-")
        self.addCleanup(self.temp.cleanup)
        self.dll = Path(self.temp.name) / "owwfd_relay.dll"
        self.dll.write_bytes(b"fixture")

    def test_single_retained_handle_is_used_for_inject_and_worker(self):
        kernel = FakeBoundKernel()
        with (
            patch.object(inject, "k32", kernel),
            patch.object(inject.ctypes, "sizeof", return_value=8),
            patch.object(inject, "_modules", side_effect=[
                [("C:\\Windows\\System32\\kernel32.dll", 0x500000000)],
                [("C:\\Windows\\System32\\kernel32.dll", 0x500000000)],
                [(str(self.dll.resolve()), 0x180000000)],
            ]),
            patch.object(inject, "_remote_load_library_from_modules", return_value=0x500001000),
            inject.BoundProcess.open(777) as proc,
        ):
            base = proc.inject_dll(self.dll)
            proc.start_export(self.dll, base, b"worker")

        self.assertEqual(base, 0x180000000)
        self.assertEqual(kernel.opens, 1)
        self.assertEqual(kernel.remote_threads[0], (0x100000001, 0x500001000, 0x700000000))
        self.assertEqual(kernel.remote_threads[1], (0x100000001, 0x180001234, None))
        self.assertEqual(kernel.freed_libs, [0x400000000])
        self.assertIn(0x100000001, kernel.closed)

    def test_exited_process_aborts_module_snapshot_and_rejects_bind(self):
        kernel = FakeBoundKernel(alive=False)
        with patch.object(inject, "k32", kernel), self.assertRaises(inject.ProcessExitedError):
            inject.BoundProcess.open(777)
        self.assertEqual(kernel.closed, [0x100000001])

    def test_process_exiting_during_snapshot_discards_result(self):
        kernel = FakeBoundKernel(alive=True)

        def snap(pid, patience=2.0):
            kernel.alive = False
            return [(str(self.dll.resolve()), 0x180000000)]

        with (
            patch.object(inject, "k32", kernel),
            patch.object(inject, "_modules", side_effect=snap),
            inject.BoundProcess.open(777) as proc,
            self.assertRaises(inject.ProcessExitedError),
        ):
            proc.modules()

    def test_worker_nonzero_exit_raises(self):
        kernel = FakeBoundKernel(worker_wait=inject.WAIT_OBJECT_0, worker_code=5)
        with (
            patch.object(inject, "k32", kernel),
            inject.BoundProcess.open(777) as proc,
            self.assertRaisesRegex(RuntimeError, "exited with 0x5"),
        ):
            proc.start_export(self.dll, 0x180000000, b"worker")

    def test_wait_failure_after_remote_thread_reports_unknown_outcome(self):
        kernel = FakeBoundKernel(thread_wait=inject.WAIT_FAILED)
        with (
            patch.object(inject, "k32", kernel),
            patch.object(inject.ctypes, "sizeof", return_value=8),
            patch.object(inject.ctypes, "get_last_error", return_value=6, create=True),
            patch.object(inject, "_modules", return_value=[("C:\\Windows\\System32\\kernel32.dll", 0x5000)]),
            patch.object(inject, "_remote_load_library_from_modules", return_value=0x500001000),
            inject.BoundProcess.open(777) as proc,
            self.assertRaisesRegex(RuntimeError, "outcome unknown"),
        ):
            proc.inject_dll(self.dll)
        self.assertEqual(kernel.freed, [])

        worker_kernel = FakeBoundKernel(worker_wait=inject.WAIT_FAILED)
        with (
            patch.object(inject, "k32", worker_kernel),
            patch.object(inject.ctypes, "get_last_error", return_value=6, create=True),
            inject.BoundProcess.open(777) as proc,
            self.assertRaisesRegex(RuntimeError, "outcome unknown"),
        ):
            proc.start_export(self.dll, 0x180000000, b"worker")


class ProtonInjectCliTests(unittest.TestCase):
    def test_inject_requires_explicit_win_pid_creation_time_and_game_exe(self):
        for argv in (
            ["--dll", "relay.dll"],
            ["--dll", "relay.dll", "--win-pid", "100"],
            ["--dll", "relay.dll", "--win-pid", "100", "--creation-time", "1234"],
        ):
            with self.assertRaises(SystemExit) as cm:
                proton_inject.main(argv)
            self.assertEqual(cm.exception.code, 2)

    def test_list_returns_nonzero_when_any_candidate_is_unreadable(self):
        with patch.object(
            proton_inject,
            "_describe_candidates",
            return_value=([(100, "C:\\ow\\Overwatch.exe", 11)], [(200, "access denied")]),
        ):
            code = proton_inject.main(["--list"])
        self.assertEqual(code, 1)

    def test_creation_time_mismatch_rejects_replacement_process(self):
        kernel = FakeBoundKernel(created_lo=9999)
        with patch.object(inject, "k32", kernel):
            code = proton_inject.main([
                "--dll",
                "relay.dll",
                "--win-pid",
                "100",
                "--creation-time",
                "1234",
                "--game-exe",
                "C:\\Games\\Overwatch\\_retail_\\Overwatch.exe",
            ])
        self.assertEqual(code, 1)

    def test_game_exe_mismatch_rejects_wrong_executable(self):
        kernel = FakeBoundKernel(created_lo=1234)
        with patch.object(inject, "k32", kernel):
            code = proton_inject.main([
                "--dll",
                "relay.dll",
                "--win-pid",
                "100",
                "--creation-time",
                "1234",
                "--game-exe",
                "C:\\Other\\Overwatch.exe",
            ])
        self.assertEqual(code, 1)


@unittest.skipUnless(sys.platform == "linux", "Linux-only /proc Proton runtime tests")
class ProtonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ow-proton-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc_root = self.root / "proc"
        self.prefix = self.root / "pfx"
        self.prefix.mkdir(parents=True)
        self.tool1 = self.root / "Proton 10"
        self.tool2 = self.root / "Proton 11"
        for tool in (self.tool1, self.tool2):
            wine = tool / "files" / "bin" / "wine"
            wine.parent.mkdir(parents=True)
            wine.write_text("#!/bin/sh\n", encoding="utf-8")
            wine.chmod(0o755)
            (tool / "files" / "lib64").mkdir(parents=True)

    def _write_proc(self, pid: int, environ: dict[str, str], maps: str = "") -> None:
        pdir = self.proc_root / str(pid)
        pdir.mkdir(parents=True, exist_ok=True)
        raw = b"\0".join(f"{k}={v}".encode() for k, v in environ.items()) + b"\0"
        (pdir / "environ").write_bytes(raw)
        if maps:
            (pdir / "maps").write_text(maps, encoding="utf-8")

    def test_resolves_single_tool_and_sync_env(self):
        self._write_proc(
            42,
            {
                "WINEPREFIX": str(self.prefix),
                "STEAM_COMPAT_TOOL_PATHS": str(self.tool1),
                "WINEFSYNC": "1",
                "WINEESYNC": "0",
            },
            maps=f"7f0000-7f1000 r-xp 00000000 00:00 1 {self.tool1.resolve()}/files/lib64/wine/ntdll.so\n",
        )
        rt = proton.resolve_proton_runtime(42, proc_root=self.proc_root)
        self.assertEqual(rt.wine_bin, self.tool1.resolve() / "files" / "bin" / "wine")
        self.assertEqual(rt.wineprefix, self.prefix.resolve())
        self.assertEqual(rt.sync_env, {"WINEFSYNC": "1", "WINEESYNC": "0"})

    def test_single_tool_not_present_in_maps_fails_closed(self):
        self._write_proc(
            42,
            {
                "WINEPREFIX": str(self.prefix),
                "STEAM_COMPAT_TOOL_PATHS": str(self.tool1),
            },
            maps=f"7f0000-7f1000 r-xp 00000000 00:00 1 {self.tool2.resolve()}/files/lib64/wine/ntdll.so\n",
        )
        with self.assertRaisesRegex(LaunchError, "None of the STEAM_COMPAT_TOOL_PATHS candidates are mapped"):
            proton.resolve_proton_runtime(42, proc_root=self.proc_root)

    def test_disambiguates_multiple_tools_using_maps(self):
        self._write_proc(
            42,
            {
                "WINEPREFIX": str(self.prefix),
                "STEAM_COMPAT_TOOL_PATHS": f"{self.tool1}:{self.tool2}",
            },
            maps=f"7f0000-7f1000 r-xp 00000000 00:00 1 {self.tool2.resolve()}/files/lib64/wine/ntdll.so\n",
        )
        rt = proton.resolve_proton_runtime(42, proc_root=self.proc_root)
        self.assertEqual(rt.tool_dir, self.tool2.resolve())

    def test_ambiguous_tools_without_maps_fail_closed(self):
        self._write_proc(42, {
            "WINEPREFIX": str(self.prefix),
            "STEAM_COMPAT_TOOL_PATHS": f"{self.tool1}:{self.tool2}",
        })
        with self.assertRaisesRegex(LaunchError, "None of the STEAM_COMPAT_TOOL_PATHS candidates are mapped"):
            proton.resolve_proton_runtime(42, proc_root=self.proc_root)

    def test_maps_substring_inside_unrelated_path_does_not_confirm_tool(self):
        fake_path = f"/unrelated/backup{self.tool2.resolve()}/files/lib64/x.so"
        self._write_proc(
            42,
            {
                "WINEPREFIX": str(self.prefix),
                "STEAM_COMPAT_TOOL_PATHS": f"{self.tool1}:{self.tool2}",
            },
            maps=f"7f0000-7f1000 r-xp 00000000 00:00 1 {fake_path}\n",
        )
        with self.assertRaisesRegex(LaunchError, "None of the STEAM_COMPAT_TOOL_PATHS candidates are mapped"):
            proton.resolve_proton_runtime(42, proc_root=self.proc_root)

    def test_relative_wineprefix_and_compat_tool_paths_fail_closed(self):
        self._write_proc(42, {
            "WINEPREFIX": "relative/pfx",
            "STEAM_COMPAT_TOOL_PATHS": str(self.tool1),
        })
        with self.assertRaisesRegex(LaunchError, "Relative WINEPREFIX"):
            proton.resolve_proton_runtime(42, proc_root=self.proc_root)

        self._write_proc(42, {
            "WINEPREFIX": str(self.prefix),
            "STEAM_COMPAT_TOOL_PATHS": "relative/Proton",
        })
        with self.assertRaisesRegex(LaunchError, "Relative path"):
            proton.resolve_proton_runtime(42, proc_root=self.proc_root)

    def test_invalid_sync_value_fails_closed(self):
        self._write_proc(
            42,
            {
                "WINEPREFIX": str(self.prefix),
                "STEAM_COMPAT_TOOL_PATHS": str(self.tool1),
                "WINEFSYNC": "invalid",
            },
            maps=f"7f0000-7f1000 r-xp 00000000 00:00 1 {self.tool1.resolve()}/files/lib64/wine/ntdll.so\n",
        )
        with self.assertRaisesRegex(LaunchError, "Unexpected WINEFSYNC"):
            proton.resolve_proton_runtime(42, proc_root=self.proc_root)

    def test_helper_timeout_reports_unknown_outcome_without_retrying(self):
        self._write_proc(
            42,
            {
                "WINEPREFIX": str(self.prefix),
                "STEAM_COMPAT_TOOL_PATHS": str(self.tool1),
            },
            maps=f"7f0000-7f1000 r-xp 00000000 00:00 1 {self.tool1.resolve()}/files/lib64/wine/ntdll.so\n",
        )
        rt = proton.resolve_proton_runtime(42, proc_root=self.proc_root)
        win_py = self.root / "python.exe"
        win_py.write_bytes(b"exe")
        dll = self.root / "owwfd_relay.dll"
        dll.write_bytes(b"dll")

        with (
            patch.object(
                proton.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(cmd=["wine"], timeout=5.0),
            ),
            self.assertRaisesRegex(LaunchError, "outcome unknown"),
        ):
            proton.run_proton_helper(rt, win_py, dll, timeout=5.0)
